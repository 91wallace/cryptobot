import sys
import os
import shutil
import subprocess
import json
import re

def audit_command(command_str, cwd=None):
    """
    Audita comandos sugeridos antes de apresentá-los ou executá-los:
    1. Verifica executáveis no PATH do sistema.
    2. Valida caminhos de arquivos e pastas no Windows e Linux.
    3. Detecta incompatibilidades entre OS (ex: flags GNU no PowerShell ou sintaxe bash).
    """
    cwd = cwd or os.getcwd()
    is_windows = sys.platform.startswith("win")

    results = {
        "command": command_str,
        "os_runtime": "windows" if is_windows else "linux",
        "valid": True,
        "warnings": [],
        "errors": [],
        "laya_decision": "PASS"  # PASS, REJECT, CAUTION
    }

    if not command_str or not command_str.strip():
        results["valid"] = False
        results["errors"].append("Comando vazio.")
        results["laya_decision"] = "REJECT"
        return results

    cmd = command_str.strip()

    # Checagem de armadilhas entre OS
    if is_windows:
        incompatibilities = {
            r"\bexport\s+[A-Za-z0-9_]+=": "No Windows PowerShell use '$env:VAR = ...' em vez de 'export'.",
            r"\bsudo\s+": "'sudo' não existe nativamente no Windows.",
            r"\btouch\s+": "'touch' não é nativo do PowerShell (use New-Item ou Set-Content).",
            r"\bls\s+-": "PowerShell 'ls' é alias de Get-ChildItem e rejeita flags GNU como '-la'.",
            r"\brm\s+-rf\b": "Em PowerShell use 'Remove-Item -Recurse -Force' para evitar comportamento inesperado.",
            r"\bsource\s+": "Em PowerShell use '. ./script.ps1' em vez de 'source'."
        }
        for pattern, msg in incompatibilities.items():
            if re.search(pattern, cmd, re.IGNORECASE):
                results["warnings"].append(f"Incompatibilidade Windows: {msg}")
                results["laya_decision"] = "CAUTION"

    # Quebra pipes e operadores (|, ;, &&, ||)
    sub_cmds = re.split(r'[|;&]+', cmd)
    for sub in sub_cmds:
        sub = sub.strip()
        if not sub:
            continue
        parts = sub.split()
        exe = parts[0]

        # Built-ins comuns do shell que não aparecem no PATH
        shell_builtins = [
            "cd", "dir", "echo", "cls", "clear", "type", "exit",
            "get-content", "set-content", "get-item", "new-item", "remove-item",
            "select-string", "select-object", "where-object", "write-output"
        ]

        if exe.lower() not in shell_builtins:
            resolved_exe = shutil.which(exe)
            if not resolved_exe:
                # Checar se é caminho local (./script ou .\script)
                if exe.startswith(".") or os.path.exists(os.path.join(cwd, exe)):
                    resolved_exe = os.path.abspath(os.path.join(cwd, exe))
                else:
                    results["warnings"].append(f"Executável '{exe}' NÃO foi encontrado no PATH do sistema.")
                    results["laya_decision"] = "CAUTION"

        # Validar caminhos de argumentos
        for arg in parts[1:]:
            # Ignora flags (--flag, -f, /flag)
            if arg.startswith("-") or (is_windows and arg.startswith("/") and len(arg) > 1 and arg[1].isalpha() and len(arg) <= 3):
                continue
            # Se parecer um caminho de arquivo/diretório
            if "/" in arg or "\\" in arg or "." in arg:
                candidate_path = os.path.normpath(os.path.join(cwd, arg)) if not os.path.isabs(arg) else arg
                # Se for alvo de criação ou leitura
                if not os.path.exists(candidate_path):
                    # Se o diretório pai não existe, reporta alerta
                    parent_dir = os.path.dirname(candidate_path)
                    if parent_dir and not os.path.exists(parent_dir):
                        results["warnings"].append(f"Caminho ou diretório pai inexistente: '{arg}'.")
                        results["laya_decision"] = "CAUTION"

    if results["errors"]:
        results["valid"] = False
        results["laya_decision"] = "REJECT"

    return results

def audit_code_syntax(code_str, language):
    """
    Valida sintaxe de blocos de código sugeridos.
    """
    results = {
        "language": language,
        "valid": True,
        "errors": [],
        "laya_decision": "PASS"
    }

    if language.lower() in ["python", "py"]:
        try:
            compile(code_str, "<system1_eval>", "exec")
        except SyntaxError as err:
            results["valid"] = False
            results["errors"].append(f"Erro de sintaxe Python [Linha {err.lineno}]: {err.msg}")
            results["laya_decision"] = "REJECT"

    elif language.lower() in ["json"]:
        try:
            json.loads(code_str)
        except Exception as err:
            results["valid"] = False
            results["errors"].append(f"JSON inválido: {err}")
            results["laya_decision"] = "REJECT"

    return results

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Uso: audit.py <cmd|code> <conteúdo> [extra/cwd]"}))
        sys.exit(1)

    mode = sys.argv[1]
    target = sys.argv[2]
    extra = sys.argv[3] if len(sys.argv) > 3 else None

    if mode == "cmd":
        res = audit_command(target, cwd=extra)
    elif mode == "code":
        res = audit_code_syntax(target, language=extra or "python")
    else:
        res = {"error": f"Modo inválido: {mode}"}

    print(json.dumps(res, indent=2, ensure_ascii=False))
