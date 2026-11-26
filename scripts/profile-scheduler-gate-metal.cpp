// Isolated original Metal MUL_MAT over private captured operands. No runtime patch.
#include "skippy.h"
#include "gguf.h"
#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml-metal.h"
#include "json.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
using json=nlohmann::ordered_json;
static std::vector<float> read(const std::string & path,size_t n) {
    if(std::filesystem::file_size(path)!=n*4)throw std::runtime_error("array size");
    std::vector<float> a(n);std::ifstream f(path,std::ios::binary);f.read(reinterpret_cast<char*>(a.data()),n*4);
    if(!f)throw std::runtime_error("array read");
    for(float x:a)if(!std::isfinite(x))throw std::runtime_error("nonfinite input");return a;
}
int main(int argc,char **argv) {
 try {
    auto abi=skippy_abi_version();
    if(argc==2 && std::string(argv[1])=="--identity") {
        std::cout<<json({{"abi",{abi.major,abi.minor,abi.patch}},{"runtime_config_bytes",sizeof(skippy_runtime_config)}}).dump()<<'\n';return 0;
    }
    if(argc!=4)throw std::runtime_error("usage: gate-metal MODEL CASES OUTPUT");
    std::filesystem::path output=argv[3];if(!std::filesystem::create_directory(output))throw std::runtime_error("output exists");
    ggml_context * meta=nullptr;auto * gf=gguf_init_from_file(argv[1],{true,&meta});
    if(!gf || !meta)throw std::runtime_error("GGUF metadata");
    const char * name="blk.0.attn_gate.weight";auto * wt=ggml_get_tensor(meta,name);auto id=gguf_find_tensor(gf,name);
    if(id<0 || !wt || wt->type!=GGML_TYPE_Q4_K || wt->ne[0]!=2560 || wt->ne[1]!=4096 || wt->ne[2]!=1 || wt->ne[3]!=1)throw std::runtime_error("weight identity");
    size_t bytes=ggml_nbytes(wt),offset=gguf_get_data_offset(gf)+gguf_get_tensor_offset(gf,id),size=std::filesystem::file_size(argv[1]);
    if(bytes!=4096*144*10 || offset>size || bytes>size-offset)throw std::runtime_error("weight bounds");
    std::vector<char> weights(bytes);std::ifstream f(argv[1],std::ios::binary);f.seekg(offset);f.read(weights.data(),bytes);if(!f)throw std::runtime_error("weight read");
    auto backend=ggml_backend_metal_init();if(!backend || !ggml_backend_is_metal(backend))throw std::runtime_error("Metal required");
    json report={{"backend",ggml_backend_name(backend)},{"weight_name",name},{"weight_type","q4_K"},{"shape",{2560,4096}},{"cases",json::array()}};
    json cases;std::ifstream(argv[2])>>cases;
    for(const auto & c:cases) {
        const std::string label=c.at("label");if(label.find('/')!=std::string::npos)throw std::runtime_error("label");
        const int width=label.find("batch-trace")!=std::string::npos?16:1;
        auto input=read(c.at("input"),2560),archived=read(c.at("output"),4096);
        auto * ctx=ggml_init({16*ggml_tensor_overhead()+ggml_graph_overhead_custom(16,false),nullptr,true});if(!ctx)throw std::runtime_error("context");
        auto * w=ggml_new_tensor_2d(ctx,GGML_TYPE_Q4_K,2560,4096);auto * x=ggml_new_tensor_2d(ctx,GGML_TYPE_F32,2560,width);auto * y=ggml_mul_mat(ctx,w,x);
        ggml_set_name(w,name);ggml_set_name(x,"attn_norm-0");ggml_set_name(y,"z-0");
        if(!ggml_backend_supports_op(backend,y))throw std::runtime_error("unsupported Metal op");
        auto * graph=ggml_new_graph_custom(ctx,16,false);ggml_build_forward_expand(graph,y);
        if(ggml_graph_n_nodes(graph)!=1)throw std::runtime_error("graph drift");
        auto buffer=ggml_backend_alloc_ctx_tensors(ctx,backend);if(!buffer)throw std::runtime_error("allocation");
        ggml_backend_tensor_set(w,weights.data(),0,bytes);
        for(int lane=0;lane<width;++lane)ggml_backend_tensor_set(x,input.data(),lane*2560*4,2560*4);
        std::vector<float> a(4096*width),b(a.size());
        for(int repeat=0;repeat<2;++repeat) {
            if(ggml_backend_graph_compute(backend,graph)!=GGML_STATUS_SUCCESS)throw std::runtime_error("Metal compute");
            ggml_backend_synchronize(backend);auto & v=repeat==0?a:b;ggml_backend_tensor_get(y,v.data(),0,v.size()*4);
            for(float z:v)if(!std::isfinite(z))throw std::runtime_error("nonfinite output");
        }
        double maximum=0;bool peers=true;for(size_t i=0;i<4096;++i)maximum=std::max(maximum,std::abs(double(a[i])-archived[i]));
        for(int lane=1;lane<width;++lane)peers=peers && std::memcmp(a.data(),a.data()+lane*4096,4096*4)==0;
        std::ofstream raw(output/(label+"-metal.f32"),std::ios::binary);raw.write(reinterpret_cast<char*>(a.data()),a.size()*4);raw.close();if(!raw)throw std::runtime_error("output write");
        report["cases"].push_back({{"label",label},{"width",width},{"elements",a.size()},{"repeat_exact",std::memcmp(a.data(),b.data(),a.size()*4)==0},{"replicated_lanes_exact",peers},{"archived_first_column_exact",std::memcmp(a.data(),archived.data(),4096*4)==0},{"max_abs_vs_archived",maximum}});
        ggml_backend_buffer_free(buffer);ggml_free(ctx);
    }
    ggml_backend_free(backend);gguf_free(gf);ggml_free(meta);report["complete"]=true;
    std::ofstream(output/"reference.json")<<report.dump(2)<<'\n';return 0;
 }catch(const std::exception & e){std::cerr<<e.what()<<'\n';return 1;}
}
