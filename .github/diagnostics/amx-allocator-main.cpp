#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstdint>

int main(int argc, char ** argv) {
    if (argc != 2) return 2;
    HMODULE backend = LoadLibraryA(argv[1]);
    if (!backend) { std::fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError()); return 3; }
    using probe = int (*)(size_t);
    auto run = reinterpret_cast<probe>(GetProcAddress(backend, "zg_test_amx_allocation"));
    if (!run) { std::fprintf(stderr, "Missing actual AMX allocator test entry\n"); return 4; }
    const size_t sizes[] = {1, 63, 64, 65, 4096, 1048576};
    for (int repeat = 0; repeat < 100; ++repeat) {
        for (size_t size : sizes) {
            std::fprintf(stderr, "AMX buffer allocation/free: %zu, repeat %d\n", size, repeat);
            std::fflush(stderr);
            if (run(size)) return 5;
        }
    }
    std::puts("AMX_ALLOCATOR_PASS=600");
    FreeLibrary(backend);
    return 0;
}
