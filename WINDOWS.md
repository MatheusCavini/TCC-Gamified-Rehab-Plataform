# Rodando no Windows (Docker Desktop + WSL2)

Este guia complementa o `README-DOCKER.md` com o que muda especificamente ao
rodar a stack no Windows. Use `docker-compose.windows.yml` em vez de
`docker-compose.yml`.

> Pré-requisito de hardware: apenas o Arduino do dispositivo "Guidao"
> (`/dev/ttyACM0`) precisa de acesso serial. O antigo pipeline via ESP32 +
> micro-ROS Agent está deprecado e não existe mais nesta stack.

## Por que precisa de passos extras

- **Rede**: Docker Desktop no Windows roda os containers dentro de uma VM
  Linux (WSL2). `network_mode: host` não é confiável nesse cenário, então
  `docker-compose.windows.yml` usa uma rede bridge dedicada, publica a porta
  `10000` para a Unity e a `8765` apenas no loopback do Windows para o Noraxon.
- **Serial/USB**: o Windows não tem `/dev/ttyACM0` — ele vê `COMx`. Para o
  container Linux enxergar o Arduino, é preciso "emprestar" o dispositivo USB
  do Windows para dentro do WSL2 usando o **usbipd-win**.

## InicializaÃ§Ã£o pela tela do Unity (recomendado para uso clÃ­nico)

O build Windows da Unity abre primeiro a tela **Preparar sessÃ£o de
reabilitaÃ§Ã£o**. Selecione os dispositivos que serÃ£o usados e clique em
**INICIAR SISTEMA**. Ela chama `scripts/Start-RehabSystem.ps1`, que:

1. compartilha/conecta ao WSL2 os USBs selecionados (pedindo UAC apenas quando
   o `usbipd bind` ainda nÃ£o foi feito);
2. reutiliza a imagem Docker existente, ou a constrÃ³i na primeira execuÃ§Ã£o;
3. fecha sessÃµes Emotiv abandonadas antes de subir o nÃ³ EEG;
4. sobe somente os perfis ROS escolhidos e espera a porta 10000 ficar pronta;
5. abre o menu de jogos apenas quando o endpoint ROS estiver acessÃ­vel.

NÃ£o Ã© necessÃ¡rio informar BUSID na tela. O lanÃ§ador identifica o USB pelo nome
exibido por `usbipd list`, inclusive depois de desconectar o cabo ou reiniciar
o Windows. Caso dois adaptadores tenham descriÃ§Ãµes indistinguÃ­veis, ajuste uma
vez os padrÃµes `REHAB_GUIDAO_USB_MATCH` e `REHAB_PULMONARY_USB_MATCH` no
`.env`; o fisioterapeuta continua apenas marcando os dispositivos na tela.

Se o executÃ¡vel Unity for instalado fora desta pasta de trabalho, defina a
variÃ¡vel de ambiente do Windows `REHAB_PLATFORM_ROOT` apontando para a raiz de
`TCC-Gamified-Rehab-Plataform`. Quando o build fica dentro do repositÃ³rio Unity
ou ao lado dele, a tela encontra o repositÃ³rio ROS automaticamente.

As credenciais e configuraÃ§Ãµes especÃ­ficas da instalaÃ§Ã£o devem ficar em um
arquivo `.env` na raiz do repositÃ³rio ROS (nunca no projeto Unity ou no Git):

```dotenv
# NecessÃ¡rio somente ao selecionar EEG Emotiv.
EMOTIV_CLIENT_ID=...
EMOTIV_CLIENT_SECRET=...

# NecessÃ¡rio somente ao selecionar EMG Noraxon. Deve incluir os channel-id
# reais configurados no Acquire e usar o ambiente que possui o SDK Noraxon.
NORAXON_SENDER_COMMAND=uv run --project ..\repo_EMG_noraxon\noraxon-sdk python src\noraxon_emg\scripts\noraxon_tcp_sender.py --use-setup --channel-id "SEU_CHANNEL_ID" --host 127.0.0.1 --port 8765
```

O Emotiv Launcher/Cortex e o Noraxon Acquire continuam sendo aplicativos
proprietÃ¡rios do Windows e precisam estar abertos e configurados antes do
clique. O script pode ser diagnosticado fora da Unity com:

```powershell
.\scripts\Start-RehabSystem.ps1 -Devices guidao,emotiv
```

Os serviÃ§os opcionais do Compose usam os perfis `guidao`, `pulmonary`,
`emotiv` e `noraxon`; nÃ£o Ã© mais necessÃ¡rio comentar/descomentar YAML.

## Passo a passo

### 1. Docker Desktop com backend WSL2

Confirme em **Settings → General → "Use the WSL 2 based engine"** (é o padrão
em instalações recentes).

### 2. Instalar o usbipd-win

No **PowerShell como Administrador**:

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

### 3. "Emprestar" o Arduino do "Guidao" para o WSL2

Com o Arduino plugado, no **PowerShell como Administrador**:

```powershell
usbipd list
```

Você verá algo como:

```
BUSID  VID:PID    DEVICE                                        STATE
2-4    2341:0043  Arduino Uno (Guidao)                           Not shared
```

Compartilhe e conecte (troque `2-4` pelo BUSID real do seu Arduino):

```powershell
usbipd bind --busid 2-4
usbipd attach --wsl --busid 2-4
```

Verifique dentro do WSL2 (abra um terminal `wsl`):

```bash
ls /dev/ttyACM*
```

⚠️ **Atenção**: o `usbipd attach` precisa ser refeito toda vez que o Arduino é
desconectado/replugado, ou a máquina Windows reinicia. Se for usar em bancada
com frequência, vale automatizar com um `.ps1`:

```powershell
# reattach-guidao.ps1
$busid = "2-4"   # ajuste para o BUSID do seu Arduino
usbipd bind --busid $busid --force
usbipd attach --wsl --busid $busid
```

### 4. Build da imagem

⚠️ Com 6 serviços apontando pra mesma imagem, **não use `docker compose build`**
— fazer isso dispara um build paralelo por serviço, e todos tentam taguear a
mesma imagem ao mesmo tempo, o que quebra com
`failed to solve: image ... already exists`.

Builde a imagem **uma vez**, direto com `docker build`:

```bash
docker build -t rehab-platform:humble .
```

De preferência rode isso **dentro do terminal WSL** (melhor performance de
I/O se o repositório estiver clonado no filesystem do WSL2, ex:
`~/TCC-Gamified-Rehab-Plataform`, em vez de `/mnt/c/...`).

Depois, suba a stack normalmente (sem `--build`, já que a imagem já existe):

```bash
docker compose -f docker-compose.windows.yml up
```

Isso sobe os 6 serviços (`device_manager`, `signal_processing`,
`noraxon_tcp_receiver`, `tcp_endpoint`, `guidao_encoder`, `guidao_controller`),
todos na rede bridge `ros2_net`.

### EMG Noraxon

O Compose inicia o receptor TCP do Noraxon dentro do Docker, mas não inicia
uma fonte de sinais EMG. Para testar com sinais sintéticos enviados do Windows
ou configurar um sensor real via Noraxon Acquire, siga as instruções em
[`src/noraxon_emg/README.md`](src/noraxon_emg/README.md). Esse guia explica
como iniciar o emissor Windows e verificar os tópicos ROS de EMG.

### 5. Configurar a Unity

No menu **Robotics → ROS Settings**:

- `ROS IP Address` = `127.0.0.1` (ou `localhost`), se a Unity roda na
  **mesma máquina Windows** — o Docker Desktop publica a porta `10000`
  automaticamente no `localhost` do Windows.
- Se a Unity roda em **outra máquina** na rede local, use o IP da máquina
  Windows que está rodando os containers, e libere a porta `10000` (TCP) de
  entrada no **Firewall do Windows**.
- Porta: `10000` (igual ao setup original em Linux).

### 6. Validar

```bash
docker compose -f docker-compose.windows.yml logs -f tcp_endpoint
```

Deve aparecer o handshake de conexão assim que a Unity der Play. No console da
Unity, `"Subscribed to /hal/device_state..."` sem exceptions de socket.


EDIT ERIC: Para interromper e desligar, Control+C no terminal que está rodando o docker e depois fazer docker compose down -v 

## Troubleshooting específico do Windows

**`/dev/ttyACM0` não aparece dentro do container**
Confirme primeiro que ele aparece dentro do **WSL2** (`wsl` → `ls /dev/ttyACM*`).
Se não aparecer lá, o problema é no `usbipd attach` (dispositivo não
compartilhado, ou desconectado após o attach) — não é um problema do Docker.

**Container sobe mas não enxerga o Arduino após reiniciar o PC**
Esperado — o WSL2 "esquece" o attach do usbipd a cada boot/replug. Rode o
`usbipd attach` de novo (ou o script do passo 3) antes de subir os containers.

**Unity não conecta na porta 10000**
- Confirme que o container `tcp_endpoint` está rodando: `docker compose -f docker-compose.windows.yml ps`.
- Teste a porta do lado do Windows: `Test-NetConnection -ComputerName localhost -Port 10000` no PowerShell.
- Se a Unity estiver em outra máquina, cheque o Firewall do Windows (regra de
  entrada para TCP 10000).

**Desempenho ruim de build/IO**
Se o repositório estiver em `C:\Users\...` montado via `/mnt/c/...` no WSL2,
o I/O cruza sistemas de arquivo e fica lento. Clone o repositório direto
dentro do WSL2 (ex: `~/projetos/...`) em vez de usar o caminho do Windows.

**`failed to solve: image "docker.io/library/rehab-platform:humble": already exists`**
Isso acontece se você rodar `docker compose build` ou `docker compose up --build`
com os 6 serviços apontando pra mesma imagem — o Compose builda todos em
paralelo e eles competem pra taguear a mesma imagem. Solução: builde manualmente
com `docker build -t rehab-platform:humble .` (uma vez só) e depois suba com
`docker compose -f docker-compose.windows.yml up`, sem `--build`.
