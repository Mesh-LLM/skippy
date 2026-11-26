// Exact one-term Q4_K projection regression; no model weights or quality tolerance.
#include "ggml.h"
#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml-metal.h"
#include "json.hpp"
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>
using json=nlohmann::ordered_json;
int main() {
 try {
    auto backend=ggml_backend_metal_init();if(!backend || !ggml_backend_is_metal(backend))throw std::runtime_error("Metal required");
    json rows=json::array();bool pass=true;
    for(int width:{1,2,3,8,16,17,32}) {
        auto ctx=ggml_init({16*ggml_tensor_overhead()+ggml_graph_overhead_custom(16,false),nullptr,true});
        if(!ctx)throw std::runtime_error("context");
        auto w=ggml_new_tensor_2d(ctx,GGML_TYPE_Q4_K,2560,8);auto x=ggml_new_tensor_2d(ctx,GGML_TYPE_F32,2560,width);auto y=ggml_mul_mat(ctx,w,x);
        auto graph=ggml_new_graph_custom(ctx,16,false);ggml_build_forward_expand(graph,y);
        auto buffer=ggml_backend_alloc_ctx_tensors(ctx,backend);if(!buffer)throw std::runtime_error("buffer");
        std::vector<unsigned char> packed(8*10*144,0);std::vector<float> expected(8);
        const ggml_fp16_t scales[]={0x0401,0x040f,0x0001,0x1fff,0x2401,0x37ff,0x3c00,0x4000};
        for(int row=0;row<8;++row) {
            auto p=packed.data()+row*10*144;std::memcpy(p,&scales[row],2);p[4+1]=1;p[16]=0x10;
            expected[row]=ggml_fp16_to_fp32(scales[row]);
        }
        std::vector<float> input(2560*width,0),actual(8*width);
        for(int lane=0;lane<width;++lane)input[lane*2560+32]=1;
        ggml_backend_tensor_set(w,packed.data(),0,packed.size());ggml_backend_tensor_set(x,input.data(),0,input.size()*4);
        if(ggml_backend_graph_compute(backend,graph)!=GGML_STATUS_SUCCESS)throw std::runtime_error("compute");
        ggml_backend_synchronize(backend);ggml_backend_tensor_get(y,actual.data(),0,actual.size()*4);
        json mismatches=json::array();
        for(int lane=0;lane<width;++lane)for(int row=0;row<8;++row)if(std::memcmp(&expected[row],&actual[lane*8+row],4)!=0)
            mismatches.push_back({{"lane",lane},{"row",row},{"scale_bits",scales[row]},{"expected",expected[row]},{"actual",actual[lane*8+row]}});
        pass=pass && mismatches.empty();rows.push_back({{"width",width},{"mismatches",mismatches}});ggml_backend_buffer_free(buffer);ggml_free(ctx);
    }
    std::cout<<json({{"backend",ggml_backend_name(backend)},{"pass",pass},{"cases",rows}}).dump(2)<<'\n';ggml_backend_free(backend);return pass?0:2;
 }catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}
}
