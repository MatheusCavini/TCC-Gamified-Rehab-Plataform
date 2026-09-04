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
  `docker-compose.windows.yml` usa uma rede bridge dedicada + publica só a
  porta `10000` (a que a Unity usa) para o Windows.
- **Serial/USB**: o Windows não tem `/dev/ttyACM0` — ele vê `COMx`. Para o
  container Linux enxergar o Arduino, é preciso "emprestar" o dispositivo USB
  do Windows para dentro do WSL2 usando o **usbipd-win**.

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

⚠️ Com 5 serviços apontando pra mesma imagem, **não use `docker compose build`**
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

Isso sobe os 5 serviços (`device_manager`, `signal_processing`, `tcp_endpoint`,
`guidao_encoder`, `guidao_controller`), todos na rede bridge `ros2_net`.

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
com os 5 serviços apontando pra mesma imagem — o Compose builda todos em
paralelo e eles competem pra taguear a mesma imagem. Solução: builde manualmente
com `docker build -t rehab-platform:humble .` (uma vez só) e depois suba com
`docker compose -f docker-compose.windows.yml up`, sem `--build`.