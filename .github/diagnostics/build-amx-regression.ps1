$ErrorActionPreference = 'Stop'
$out = Join-Path $env:RUNNER_TEMP 'amx-validation'
New-Item -ItemType Directory -Force $out | Out-Null
"AMX_ARTIFACT=$out" >> $env:GITHUB_ENV
$source = Join-Path $out 'llama.cpp'
git clone --depth 1 --branch b8390 https://github.com/ggml-org/llama.cpp.git $source
if ($LASTEXITCODE -ne 0) { throw 'Source clone failed' }
git -C $source rev-parse HEAD | Out-File (Join-Path $out 'llama-source-sha.txt')
$amx = Join-Path $source 'ggml/src/ggml-cpu/amx/amx.cpp'
# This test entry calls the real allocation and destruction callbacks; it never runs AMX compute.
$entry = @'
#if defined(__AMX_INT8__) && defined(__AVX512VNNI__)
extern "C" __declspec(dllexport) int zg_test_amx_allocation(size_t size) {
    ggml_backend_buffer_t buffer = ggml_backend_amx_buffer_type_alloc_buffer(nullptr, size);
    if (!buffer) return 1;
    ggml_backend_buffer_free(buffer);
    return 0;
}
#endif
'@
Add-Content -LiteralPath $amx -Value $entry
$build = Join-Path $out 'build'
cmake -S $source -B $build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_WINDOWS_EXPORT_ALL_SYMBOLS=ON -DCMAKE_C_FLAGS_RELEASE='-O2 -DNDEBUG -Xclang --dependent-lib=msvcrt' -DCMAKE_CXX_FLAGS_RELEASE='-O2 -DNDEBUG -Xclang --dependent-lib=msvcrt' -DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=ON -DGGML_BACKEND_DL=ON -DGGML_OPENMP=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=OFF -DLLAMA_BUILD_SERVER=OFF -DLLAMA_CURL=OFF
if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed' }
cmake --build $build --target ggml-cpu-sapphirerapids --parallel 4
if ($LASTEXITCODE -ne 0) { throw 'AMX backend build failed' }
$bin = Join-Path $build 'bin'
$old = Join-Path $out 'original'
New-Item -ItemType Directory -Force $old | Out-Null
Copy-Item (Join-Path $bin '*.dll') $old
clang++ .github/diagnostics/amx-allocator-main.cpp -O2 -Xclang --dependent-lib=msvcrt -o (Join-Path $out 'amx-allocator.exe')
if ($LASTEXITCODE -ne 0) { throw 'Regression executable build failed' }
$rows = @()
function Run-Allocator-Probe($variant, $directory) {
    $exe = Join-Path $out 'amx-allocator.exe'
    $dll = Join-Path $directory 'ggml-cpu-sapphirerapids.dll'
    $pass = 0; $fail = 0; $codes = @()
    for ($i = 1; $i -le 5; $i++) {
        $proc = Start-Process -FilePath $exe -ArgumentList $dll -WorkingDirectory $directory -Wait -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $out "$variant-$i.stdout.log") -RedirectStandardError (Join-Path $out "$variant-$i.stderr.log")
        $codes += $proc.ExitCode
        if ($proc.ExitCode -eq 0) { $pass++ } else { $fail++ }
    }
    return @{ variant=$variant; passed=$pass; failed=$fail; exitCodes=$codes; allocationsPerPassingProcess=600 }
}
$rows += Run-Allocator-Probe 'original' $old
# Apply only the allocator correction to the actual production callback.
$text = [IO.File]::ReadAllText($amx)
$before = '    free(buffer->context);'
if (($text.Split($before).Count - 1) -ne 1) { throw 'Expected exactly one free callback' }
[IO.File]::WriteAllText($amx, $text.Replace($before, '    ggml_aligned_free(buffer->context, buffer->size);'))
git -C $source diff -- ggml/src/ggml-cpu/amx/amx.cpp | Out-File (Join-Path $out 'tested-source.diff')
cmake --build $build --target ggml-cpu-sapphirerapids --parallel 4
if ($LASTEXITCODE -ne 0) { throw 'Patched AMX backend build failed' }
$fixed = Join-Path $out 'patched'
New-Item -ItemType Directory -Force $fixed | Out-Null
Copy-Item (Join-Path $bin '*.dll') $fixed
$rows += Run-Allocator-Probe 'patched' $fixed
$rows | ConvertTo-Json -Depth 6 | Tee-Object -FilePath (Join-Path $out 'allocator-results.json')
if ($rows[0].failed -ne 5 -or $rows[1].passed -ne 5) { throw 'Allocator negative/positive controls failed' }
