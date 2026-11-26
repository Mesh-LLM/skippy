// Diagnostic public llama caller. Links the original native bundle unchanged.
#include "skippy.h"
#include "ggml-backend.h"
#include "json.hpp"
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>
using json = nlohmann::ordered_json;

struct Trace {
    std::filesystem::path directory;
    int step = -1;
    int width = 0;
    json rows = json::array();
    std::string error;
};

static bool observe(ggml_tensor * t, bool ask, void * data) {
    auto & trace = *static_cast<Trace *>(data);
    // Only logical 2D token-column tensors. Do not interpret recurrent/state/head layouts.
    const bool wanted = trace.step >= 1 && trace.step <= 3 && t->op != GGML_OP_NONE &&
        t->type == GGML_TYPE_F32 && t->nb[0] == sizeof(float) && t->ne[0] > 0 &&
        t->ne[0] <= 300000 && t->ne[1] == trace.width && t->ne[2] == 1 && t->ne[3] == 1;
    if (ask) return wanted;
    if (!wanted) return true;
    try {
        std::vector<float> values(static_cast<size_t>(t->ne[0]));
        ggml_backend_tensor_get(t, values.data(), 0, values.size() * sizeof(float));
        for (float value : values) if (!std::isfinite(value)) throw std::runtime_error("nonfinite activation");
        const auto file = "activation-" + std::to_string(trace.rows.size()) + ".f32";
        std::ofstream raw(trace.directory/file, std::ios::binary);
        raw.write(reinterpret_cast<const char *>(values.data()), values.size() * sizeof(float));raw.close();
        if (!raw) throw std::runtime_error("activation write failed");
        json sources = json::array();
        for (auto * source : t->src) if (source) sources.push_back({{"name",source->name},
            {"shape",{source->ne[0],source->ne[1],source->ne[2],source->ne[3]}}});
        trace.rows.push_back({{"step",trace.step},{"name",t->name},{"op",ggml_op_name(t->op)},
            {"shape",{t->ne[0],t->ne[1],t->ne[2],t->ne[3]}},
            {"strides",{t->nb[0],t->nb[1],t->nb[2],t->nb[3]}},{"sources",sources},
            {"raw_file",file},{"elements",values.size()}});
        return true;
    } catch (const std::exception & error) { trace.error = error.what(); return false; }
}

static json logits(llama_context * ctx, int row, const llama_vocab * vocab,
                   const std::filesystem::path & directory, int step) {
    const auto count = llama_vocab_n_tokens(vocab);const float * values = llama_get_logits_ith(ctx,row);
    if (!values) throw std::runtime_error("missing logits");
    std::vector<int> order(static_cast<size_t>(count));std::iota(order.begin(),order.end(),0);
    for (int i=0;i<count;++i) if (!std::isfinite(values[i])) throw std::runtime_error("nonfinite logits");
    std::partial_sort(order.begin(),order.begin()+20,order.end(),[&](int a,int b){return values[a]!=values[b]?values[a]>values[b]:a<b;});
    auto winner=std::find_if(order.begin(),order.begin()+20,[&](int i){return !llama_vocab_is_eog(vocab,i);});
    if (winner==order.begin()+20) throw std::runtime_error("no non-EOG top20 candidate");
    const auto file="subject-logits-"+std::to_string(step)+".f32";
    std::ofstream raw(directory/file,std::ios::binary);raw.write(reinterpret_cast<const char *>(values),count*sizeof(float));raw.close();
    if (!raw) throw std::runtime_error("logits write failed");
    json top=json::array();for (int i=0;i<20;++i) top.push_back({{"token",order[i]},{"logit",values[order[i]]}});
    return {{"step",step},{"token",*winner},{"raw_file",file},{"top",top}};
}

int main(int argc,char **argv) {
    try {
        const auto abi=skippy_abi_version();
        if (argc==2 && std::string(argv[1])=="--identity") {
            std::cout<<json({{"abi",{abi.major,abi.minor,abi.patch}},{"runtime_config_bytes",sizeof(skippy_runtime_config)}}).dump()<<'\n';return 0;
        }
        if (argc!=5) throw std::runtime_error("usage: activation-probe MODEL PROMPT MODE OUTPUT");
        const std::string mode=argv[3];const bool tracing=mode=="solo-trace"||mode=="batch-trace";
        if (mode!="solo-plain"&&mode!="batch-plain"&&!tracing) throw std::runtime_error("unregistered mode");
        const int lanes=mode.rfind("solo",0)==0?1:16;const auto directory=std::filesystem::path(argv[4]);
        if (!std::filesystem::create_directory(directory)) throw std::runtime_error("output must be new");
        std::ifstream input(argv[2],std::ios::binary);if (!input) throw std::runtime_error("missing prompt");
        const std::string prompt((std::istreambuf_iterator<char>(input)),{});
        ggml_backend_load_all();auto mp=llama_model_default_params();mp.n_gpu_layers=999;
        auto * model=llama_model_load_from_file(argv[1],mp);if (!model) throw std::runtime_error("model load failed");
        Trace trace;trace.directory=directory;
        auto cp=llama_context_default_params();cp.n_ctx=131072;cp.n_batch=cp.n_ubatch=512;
        cp.n_seq_max=32;cp.n_rs_seq=0;cp.n_outputs_max_per_seq=1;cp.n_threads=cp.n_threads_batch=16;
        cp.kv_unified=true;cp.flash_attn_type=LLAMA_FLASH_ATTN_TYPE_ENABLED;cp.type_k=cp.type_v=GGML_TYPE_F16;
        if (tracing) {cp.cb_eval=observe;cp.cb_eval_user_data=&trace;}
        auto * ctx=llama_init_from_model(model,cp);if (!ctx) throw std::runtime_error("context failed");
        const auto * vocab=llama_model_get_vocab(model);std::vector<llama_token> tokens(8192);
        const int count=llama_tokenize(vocab,prompt.data(),static_cast<int32_t>(prompt.size()),tokens.data(),8192,true,true);
        if (count!=512) throw std::runtime_error("tokenizer count drift");tokens.resize(count);
        json report={{"mode",mode},{"abi",{abi.major,abi.minor,abi.patch}},{"prompt_ids",tokens},
            {"calls",json::array()},{"subject_steps",json::array()},{"sampling","greedy non-EOG; calibration required"}};
        std::ofstream(directory/"ready.json")<<report.dump(2)<<'\n';std::string go;
        if (!std::getline(std::cin,go)||go!="GO") throw std::runtime_error("not admitted");
        auto batch=llama_batch_init(512,0,1);std::vector<std::vector<llama_token>> generated(lanes);
        auto decode=[&](int lane,int step) {
            const bool prefill=step==0;batch.n_tokens=prefill?512:lanes;
            for (int i=0;i<batch.n_tokens;++i) {
                const int seq=prefill?lane:i;batch.token[i]=prefill?tokens[i]:generated[i].back();
                batch.pos[i]=prefill?i:511+step;batch.n_seq_id[i]=1;batch.seq_id[i][0]=seq;
                batch.logits[i]=!prefill||i==511;
            }
            trace.step=prefill?-1:step;trace.width=lanes;
            if (llama_decode(ctx,batch)!=0) throw std::runtime_error("decode failed: "+trace.error);
            for (int i=0;i<(prefill?1:lanes);++i) {
                const int seq=prefill?lane:i;const int row=prefill?511:i;
                if (seq==0) {auto s=logits(ctx,row,vocab,directory,step);generated[seq].push_back(s["token"].get<int>());report["subject_steps"].push_back(s);}
                else {
                    const float * values=llama_get_logits_ith(ctx,row);if (!values) throw std::runtime_error("missing lane logits");
                    int best=-1;for (int k=0;k<llama_vocab_n_tokens(vocab);++k) {
                        if (!std::isfinite(values[k])) throw std::runtime_error("nonfinite lane logits");
                        if (!llama_vocab_is_eog(vocab,k)&&(best<0||values[k]>values[best])) best=k;
                    }
                    generated[seq].push_back(best);
                }
            }
            report["calls"].push_back({{"phase",prefill?"prefill":"decode"},{"lane",prefill?lane:-1},{"step",step},{"batch_tokens",batch.n_tokens}});
        };
        for (int lane=0;lane<lanes;++lane) decode(lane,0);
        for (int step=1;step<32;++step) decode(-1,step);
        report["lanes"]=json::array();
        for (int i=0;i<lanes;++i) {
            std::vector<char> text(32768);const int size=llama_detokenize(vocab,generated[i].data(),32,text.data(),static_cast<int32_t>(text.size()),false,false);
            if (size<0) throw std::runtime_error("detokenize failed");
            report["lanes"].push_back({{"lane",i},{"generated_ids",generated[i]},{"text",std::string(text.data(),size)}});
        }
        if (!trace.error.empty()) throw std::runtime_error(trace.error);
        report["activations"]=trace.rows;report["complete"]=true;
        std::ofstream(directory/"probe.json")<<report.dump(2)<<'\n';llama_batch_free(batch);llama_free(ctx);llama_model_free(model);
        return 0;
    } catch (const std::exception &error) {std::cerr<<"ACTIVATION_PROBE_FAILURE "<<error.what()<<'\n';return 1;}
}
