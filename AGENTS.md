# System 1 & Laya Core Operational Directives

Este projeto opera sob o paradigma de cognição em dois sistemas: **System 1 (Decisão Rápida & Auditoria Contínua)** e **System 2 (LLM / Raciocínio Profundo)**.

---

## 1. Princípio Intrínseco do System 1 (Pré-LLM)

Antes de propor qualquer alteração extensa, pesquisa cega ou planejamento:
1. **Decisões Tipadas Fechadas:** Sempre que houver classificação categórica (Choice/Score/Noul), roteamento de ferramentas ou triagem de intenções, priorize a camada não-autoregressiva e evite gastar tokens gerando explicações prolixas desnecessárias.
2. **Descoberta Comportamental:** Utilize o motor comportamental `jg` (Jevgrep) antes de buscas de texto bruto genéricas.

---

## 2. Auditoria Obrigatória Pós-LLM (Anti-Alucinação Operacional)

A LLM **nunca** deve afirmar que um comando ou script "vai funcionar de primeira" sem que o **System 1** tenha auditado:

### A. Auditoria de Comandos de Terminal
Antes de sugerir um comando para o usuário ou executá-lo:
* **Validação de PATH:** Verificar se os binários e executáveis realmente existem no ambiente local (`shutil.which` / runtime).
* **Validação de Sistema Operacional:** Bloquear ou converter comandos incompatíveis com o ambiente (ex.: comandos Linux como `ls -la`, `export`, `source`, `touch` rodando no Windows/PowerShell sem adaptação).
* **Validação de Caminhos:** Checar se diretórios e arquivos referenciados existem no disco antes de declarar como prontos.
* **Ferramenta de Auditoria:** O script [`system1_audit.py`](file:///C:/Users/pece/projects/cryptobot/system1_audit.py) deve ser acionado para obter a decisão de decisão (`PASS`, `CAUTION` ou `REJECT`).

### B. Auditoria de Código e Lógica
* Qualquer snippet sugerido (Python, JSON, etc.) deve passar por validação de sintaxe e compilação estática (`compile()`, `json.loads`) antes de ser entregue.
* Caso haja erros ou alertas de runtime, o agente deve corrigir a proposta internamente antes de emitir a resposta final.

---

## 3. Diretriz de Acesso e Operação na VM Oracle

Sempre que o usuário mencionar **"na VM"**, **"no servidor"**, **"em produção"** ou pedir para verificar/resolver algo no ambiente remoto:
* **Host SSH:** `oracle-vm` (configurado em `~/.ssh/config` com IP `<ORACLE_VM_IP>`, usuário `<SSH_USER>` e chave `~/.ssh/<CHAVE_SSH>.key`).
* **Diretório do Projeto Remoto:** `/home/<SSH_USER>/cryptobot`
* **Ambiente Virtual Remoto:** `/home/<SSH_USER>/cryptobot/.venv`
* **Controle de Execução:** Utilizar diretamente os comandos e scripts remotos (`start`, `status`, `stop` ou `ssh oracle-vm "..."`).
* **Sincronização de Código:** Para atualizar código na VM sem usar GitHub, enviar arquivos via `scp <arquivo> oracle-vm:/home/<SSH_USER>/cryptobot/<caminho>` ou pacote `.zip`.

