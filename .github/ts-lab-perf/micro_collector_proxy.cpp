// Standalone proxy ONLY. NOT the ClickHouse ContextTimeSeriesTagsCollector.
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>
#include <sys/resource.h>

using Clock=std::chrono::steady_clock;
static uint64_t mix(uint64_t x) {
    x+=0x9e3779b97f4a7c15ULL;
    x=(x^(x>>30))*0xbf58476d1ce4e5b9ULL;
    x=(x^(x>>27))*0x94d049bb133111ebULL;
    return x^(x>>31);
}
struct Slot {uint64_t id=0; uint32_t group=0;};
struct Flat {
    std::vector<Slot> cells;
    std::vector<uint8_t> used;
    size_t mask=0;
    explicit Flat(size_t n) {
        size_t size=1;
        while(size<n*2+1)size*=2;
        cells.resize(size);
        used.resize(size);
        mask=size-1;
    }
    void insert(uint64_t id,uint32_t group) {
        size_t k=mix(id)&mask;
        while(used[k] && cells[k].id!=id) k=(k+1)&mask;
        if(used[k])throw std::logic_error("duplicate ID");
        used[k]=1;cells[k]={id,group};
    }
    uint32_t lookup(uint64_t id) const {
        size_t k=mix(id)&mask;
        while(used[k]) {
            if(cells[k].id==id)return cells[k].group;
            k=(k+1)&mask;
        }
        throw std::logic_error("missing ID");
    }
};
static double ms(Clock::time_point start) {
    return std::chrono::duration<double,std::milli>(Clock::now()-start).count();
}
static long long peak_rss_kib() {
    rusage v{};
    if(getrusage(RUSAGE_SELF,&v)!=0)return -1;
    return v.ru_maxrss;
}
int main(int argc,char** argv) {
    size_t n=100000;uint32_t g=38;
    std::string variant="stage_hash",order="sequential";
    uint64_t seed=88199;
    for(int i=1;i<argc;i++){
        auto need=[&](){if(i+1>=argc)throw std::runtime_error("missing flag value");return std::string(argv[++i]);};
        std::string k=argv[i];
        if(k=="--n")n=std::stoull(need());
        else if(k=="--groups")g=std::stoul(need());
        else if(k=="--variant")variant=need();
        else if(k=="--order")order=need();
        else if(k=="--seed")seed=std::stoull(need());
        else throw std::runtime_error("unknown flag "+k);
    }
    if(n==0||g==0||n>2000000||g>100000||!(
        variant=="full_hash"||variant=="stage_hash"||variant=="stage_flat"))
        throw std::runtime_error("invalid N, G or variant");
    std::unordered_map<std::string,uint32_t> group_lookup;
    std::vector<uint32_t> group_to_stage;
    std::unordered_map<uint64_t,uint32_t> id_lookup;
    group_lookup.reserve(variant=="full_hash"?n:g);
    if(variant!="stage_flat")id_lookup.reserve(n);
    Flat flat(variant=="stage_flat"?n:0);
    const uint64_t id_seed=seed*37;
    auto stage_key=[](uint32_t x){return "stage-"+std::to_string(x);};
    auto begin=Clock::now();
    for(size_t i=0;i<n;i++) {
        uint32_t stage=static_cast<uint32_t>(i%g);
        // full input has unique 'build' and 'branch'. projected input only stage.
        std::string key=stage_key(stage);
        if(variant=="full_hash")key+="|build-"+std::to_string(i)+"|branch-main";
        auto [it,inserted]=group_lookup.try_emplace(key,static_cast<uint32_t>(group_to_stage.size()));
        if(inserted)group_to_stage.push_back(stage);
        uint64_t id=mix(static_cast<uint64_t>(i)+id_seed);
        if(variant=="stage_flat")flat.insert(id,it->second);
        else id_lookup.emplace(id,it->second);
    }
    auto register_ms=ms(begin);
    std::vector<uint32_t> permutation;
    if(order=="random"){
        permutation.resize(n);
        std::iota(permutation.begin(),permutation.end(),uint32_t(0));
        std::mt19937 rng(static_cast<uint32_t>(seed));
        std::shuffle(permutation.begin(),permutation.end(),rng);
    } else if(order!="sequential")throw std::runtime_error("order must be sequential/random");
    begin=Clock::now();
    // A real stage-group output, checked across algorithm variants.
    std::vector<uint64_t> sums(g,0);
    for(size_t j=0;j<n;j++){
        size_t i=order=="random"?permutation[j]:j;
        uint64_t id=mix(static_cast<uint64_t>(i)+id_seed);
        uint32_t group=variant=="stage_flat"?flat.lookup(id):id_lookup.at(id);
        uint32_t stage=group_to_stage.at(group);
        sums[stage]+=(i%997)+1;
    }
    auto lookup_ms=ms(begin);
    uint64_t checksum=0;
    for(uint32_t i=0;i<g;i++)checksum+=sums[i]*(uint64_t(i)+1);
    if(checksum==0 || group_lookup.size()!=(variant=="full_hash"?n:std::min<size_t>(n,g)))
        throw std::runtime_error("invalid checksum/groups");
    std::cout<<std::fixed<<std::setprecision(4)
             <<"{\"variant\":\""<<variant<<"\",\"n\":"<<n<<",\"groups\":"<<g
             <<",\"group_registry_size\":"<<group_lookup.size()
             <<",\"id_count\":"<<n<<",\"order\":\""<<order<<"\""
             <<",\"register_ms\":"<<register_ms<<",\"lookup_ms\":"<<lookup_ms
             <<",\"peak_rss_kib\":"<<peak_rss_kib()
             <<",\"output_checksum\":"<<checksum
             <<",\"model_not_clickhouse\":true}\n";
    return 0;
}
