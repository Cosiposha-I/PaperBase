# Запуск paperbase на GPU (через изолированный venv с CUDA-torch).
# Пример: .\pb-gpu.ps1 search "катализаторы переэтерификации" --k 8
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m paperbase @args
