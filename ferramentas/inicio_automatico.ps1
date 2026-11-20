<#
  Cria ou remove o atalho "Monitor de Senha Feegow" na pasta Inicializar do
  Windows (shell:startup) do usuario atual. Nao precisa de administrador.

  Uso (normalmente pelos .bat da pasta principal):
    powershell -ExecutionPolicy Bypass -File ferramentas\inicio_automatico.ps1 -Acao instalar
    powershell -ExecutionPolicy Bypass -File ferramentas\inicio_automatico.ps1 -Acao remover
  Opcional: -Python "C:\caminho\pythonw.exe"  (quando o pythonw nao esta no PATH)
#>
param(
    [ValidateSet("instalar", "remover")][string]$Acao = "instalar",
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$pasta    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$startup  = [Environment]::GetFolderPath("Startup")
$atalho   = Join-Path $startup "Monitor de Senha Feegow.lnk"
$pyw      = Join-Path $pasta "iniciar_monitor.pyw"
$exe      = Join-Path $pasta "MonitorFeegow.exe"

# Atalhos antigos que abriam a cadeia BAT -> py -> py (evita abrir duas vezes)
$antigos = Get-ChildItem -Path $startup -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object {
    $alvo = (New-Object -ComObject WScript.Shell).CreateShortcut($_.FullName)
    ($alvo.TargetPath + " " + $alvo.Arguments) -match "executar_iniciar_explica|iniciar_explica\.py|executar_invisivel_explica|SCRIPT_EXPLICA\.py"
}

if ($Acao -eq "remover") {
    if (Test-Path $atalho) { Remove-Item $atalho -Force; Write-Host "Removido: $atalho" }
    else { Write-Host "Nao havia atalho em $atalho" }
    foreach ($a in $antigos) { Write-Host "Aviso: ainda existe um atalho antigo: $($a.FullName)" }
    exit 0
}

if (Test-Path $pyw) {
    $prefixo = ""
    if (-not $Python) {
        # Sem caminho fixo: WinPython na pasta do monitor (ou 1-2 niveis acima),
        # pythonw.exe do PATH e, por ultimo, o "pyw" launcher (pyw.exe -3).
        $bases = @($pasta, (Split-Path -Parent $pasta), (Split-Path -Parent (Split-Path -Parent $pasta))) |
            Where-Object { $_ }
        $winpython = foreach ($b in $bases) {
            Get-ChildItem -Path (Join-Path $b "Winpython*\WPy64-*\python-*\pythonw.exe") -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty FullName
        }
        $candidatos = @(
            ($winpython | Select-Object -First 1),
            (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
        ) | Where-Object { $_ -and (Test-Path $_) }
        $Python = $candidatos | Select-Object -First 1
        if (-not $Python) {
            $Python = Get-Command pyw.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
            if ($Python) { $prefixo = "-3 " }
        }
    }
    if (-not $Python) { Write-Host "pythonw.exe nao encontrado. Rode de novo com -Python <caminho do pythonw.exe>."; exit 1 }
    $alvo = $Python; $argumentos = "$prefixo`"$pyw`""
} elseif (Test-Path $exe) {
    $alvo = $exe; $argumentos = ""
} else {
    Write-Host "Nem iniciar_monitor.pyw nem MonitorFeegow.exe em $pasta"; exit 1
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($atalho)
$lnk.TargetPath = $alvo
$lnk.Arguments = $argumentos
$lnk.WorkingDirectory = $pasta
$lnk.WindowStyle = 7
$lnk.Description = "Monitor de Senha do Feegow (inicia com o Windows)"
$lnk.Save()
Write-Host "Criado: $atalho"
Write-Host "  -> $alvo $argumentos"
foreach ($a in $antigos) {
    Write-Host "ATENCAO: atalho antigo encontrado: $($a.FullName)"
    Write-Host "         Apague-o para o monitor nao abrir duas vezes (o novo ja faz tudo)."
}
