// Offline arithmetic reference over pinned GGUF bytes and existing private captures.
#include "skippy.h"
#include "gguf.h"
#include "json.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
using json = nlohmann::ordered_json;

static std::vector<float> floats(const std::filesystem::path & path,size_t count) {
    if (std::filesystem::file_size(path)!=count*sizeof(float)) throw std::runtime_error("float input size drift");
    std::vector<float> out(count);std::ifstream file(path,std::ios::binary);
    file.read(reinterpret_cast<char *>(out.data()),count*sizeof(float));
    if (!file) throw std::runtime_error("input read failed");
    for (float x:out) if (!std::isfinite(x)) throw std::runtime_error("nonfinite input");
    return out;
}

int main(int argc,char **argv) {
    try {
        const auto abi=skippy_abi_version();
        if (argc==2 && std::string(argv[1])=="--identity") {
            std::cout<<json({{"abi",{abi.major,abi.minor,abi.patch}},{"runtime_config_bytes",sizeof(skippy_runtime_config)}}).dump()<<'\n';return 0;
        }
        if (argc!=4) throw std::runtime_error("usage: projection-reference MODEL CASES PRIVATE_OUTPUT");
        const std::filesystem::path output=argv[3];
        if (!std::filesystem::create_directory(output)) throw std::runtime_error("output must be new");
        std::ifstream in(argv[2]);json cases;in>>cases;
        ggml_context * ctx=nullptr;gguf_init_params params{true,&ctx};
        auto * gguf=gguf_init_from_file(argv[1],params);if (!gguf || !ctx) throw std::runtime_error("GGUF metadata load failed");
        const char * name="blk.0.attn_gate.weight";const int64_t id=gguf_find_tensor(gguf,name);
        auto * tensor=ggml_get_tensor(ctx,name);
        if (id<0 || !tensor || tensor->ne[0]!=2560 || tensor->ne[1]!=4096 || tensor->ne[2]!=1 || tensor->ne[3]!=1) throw std::runtime_error("weight shape drift");
        const auto type=gguf_get_tensor_type(gguf,id);const size_t rowbytes=ggml_row_size(type,2560);
        const size_t bytes=ggml_nbytes(tensor);if (bytes!=rowbytes*4096) throw std::runtime_error("weight layout drift");
        const size_t offset=gguf_get_data_offset(gguf)+gguf_get_tensor_offset(gguf,id);
        if (offset>std::filesystem::file_size(argv[1]) || bytes>std::filesystem::file_size(argv[1])-offset) throw std::runtime_error("weight file bounds");
        std::vector<char> packed(bytes);std::ifstream model(argv[1],std::ios::binary);model.seekg(offset);model.read(packed.data(),bytes);
        if (!model) throw std::runtime_error("weight read failed");
        std::ofstream rawweight(output/"weight.bin",std::ios::binary);rawweight.write(packed.data(),bytes);rawweight.close();
        if (!rawweight) throw std::runtime_error("weight snapshot failed");
        const auto * traits=ggml_get_type_traits(type);
        if (type!=GGML_TYPE_F32 && (!traits || !traits->to_float)) throw std::runtime_error("no pinned dequantizer");
        const double u32=std::ldexp(1.0,-24),u64=std::ldexp(1.0,-53);
        const double gamma32=5120*u32/(1-5120*u32),gamma64=2559*u64/(1-2559*u64);
        json report={{"weight_name",name},{"weight_type",ggml_type_name(type)},{"shape",{2560,4096}},
            {"weight_offset",offset},{"weight_bytes",bytes},{"row_bytes",rowbytes},{"cases",json::array()},
            {"fp32_gamma_5120",gamma32},{"fp64_gamma_2559",gamma64},
            {"scope","CPU diagnostic only; no model generation or performance measurement. Generic FP32 forward-error bound is not a quality tolerance or proof of the Metal algorithm."}};
        std::vector<float> weight(2560);
        for (const auto & c:cases) {
            const std::string label=c.at("label");if (label.find('/')!=std::string::npos) throw std::runtime_error("unsafe label");
            const auto input=floats(c.at("input").get<std::string>(),2560),actual=floats(c.at("output").get<std::string>(),4096);
            std::vector<double> reference(4096),compensated(4096),input16(4096),weight16(4096),both16(4096);
            double maxerror=0,error2=0,maxratio=0,max64=0,maxcomp=0,reference2=0;size_t outside=0,worst=0;
            for (size_t row=0;row<4096;++row) {
                if (type==GGML_TYPE_F32) std::memcpy(weight.data(),packed.data()+row*rowbytes,rowbytes);
                else traits->to_float(packed.data()+row*rowbytes,weight.data(),2560);
                double sum=0,kahan=0,correction=0,absolute=0,si=0,sw=0,sb=0;
                for (size_t k=0;k<2560;++k) {
                    if (!std::isfinite(weight[k])) throw std::runtime_error("nonfinite weight");
                    const double product=static_cast<double>(weight[k])*static_cast<double>(input[k]);
                    sum+=product;absolute+=std::abs(product);
                    const double xi=ggml_fp16_to_fp32(ggml_fp32_to_fp16(input[k]));
                    const double wi=ggml_fp16_to_fp32(ggml_fp32_to_fp16(weight[k]));
                    si+=static_cast<double>(weight[k])*xi;sw+=wi*static_cast<double>(input[k]);sb+=wi*xi;
                    const double y=product-correction,next=kahan+y;correction=(next-kahan)-y;kahan=next;
                }
                reference[row]=sum;compensated[row]=kahan;input16[row]=si;weight16[row]=sw;both16[row]=sb;
                const double error=std::abs(static_cast<double>(actual[row])-sum),bound64=gamma64*absolute,bound32=gamma32*absolute;
                if (!std::isfinite(sum)||!std::isfinite(error)) throw std::runtime_error("nonfinite reference");
                if (error>maxerror) {maxerror=error;worst=row;}
                error2+=error*error;reference2+=sum*sum;max64=std::max(max64,bound64);maxcomp=std::max(maxcomp,std::abs(sum-kahan));
                if (error>bound32+bound64) ++outside;
                if (bound32+bound64>0) maxratio=std::max(maxratio,error/(bound32+bound64));
            }
            for (const auto & item:std::vector<std::pair<std::string,const std::vector<double> *>>{{"reference",&reference},{"compensated",&compensated},{"input16",&input16},{"weight16",&weight16},{"both16",&both16}}) {
                std::ofstream file(output/(label+"-"+item.first+".f64"),std::ios::binary);
                file.write(reinterpret_cast<const char *>(item.second->data()),4096*sizeof(double));file.close();
                if (!file) throw std::runtime_error("reference write failed");
            }
            json rounded=json::object();
            for (const auto & item:std::vector<std::pair<std::string,const std::vector<double> *>>{{"input16",&input16},{"weight16",&weight16},{"both16",&both16}}) {
                double maximum=0,squared=0,norm=0;
                for (size_t row=0;row<4096;++row) {
                    const double value=(*item.second)[row],error=static_cast<double>(actual[row])-value;
                    if (!std::isfinite(value)) throw std::runtime_error("nonfinite rounded reference");
                    maximum=std::max(maximum,std::abs(error));squared+=error*error;norm+=value*value;
                }
                rounded[item.first]={{"max_abs_error",maximum},{"rms_error",std::sqrt(squared/4096)},
                    {"relative_l2_error",norm>0?std::sqrt(squared/norm):0}};
            }
            report["cases"].push_back({{"label",label},{"rounded_reference_errors",rounded},{"elements",4096},{"max_abs_error",maxerror},{"rms_error",std::sqrt(error2/4096)},
                {"relative_l2_error",reference2>0?std::sqrt(error2/reference2):0},{"max_error_over_generic_fp32_bound",maxratio},
                {"outside_generic_fp32_bound",outside},{"max_fp64_sum_bound",max64},{"max_plain_vs_compensated",maxcomp},
                {"worst_row",worst},{"worst_actual",actual[worst]},{"worst_reference",reference[worst]}});
        }
        report["complete"]=true;std::ofstream(output/"reference.json")<<report.dump(2)<<'\n';gguf_free(gguf);ggml_free(ctx);return 0;
    } catch (const std::exception & error) {std::cerr<<"PROJECTION_REFERENCE_FAILURE "<<error.what()<<'\n';return 1;}
}
