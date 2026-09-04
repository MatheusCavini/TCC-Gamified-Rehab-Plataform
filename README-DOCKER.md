# Dockerização — TCC Gamified Rehab Platform

Estratégia e arquivos para rodar o workspace ROS2 Humble (`MatheusCavini/TCC-Gamified-Rehab-Plataform`)
dentro de containers, preservando as duas particularidades do projeto:

1. **Comunicação TCP com a Unity** (`ros_tcp_endpoint`, porta `10000`)
2. **Comunicação serial** com o Arduino do dispositivo "Guidao" em `/dev/ttyACM0`,
   acessado via **pyserial** diretamente pelo nó `encoder_guidao_node` — sem
   nenhum microcontrolador rodando ROS2/micro-ROS.

> ⚠️ **Atualização**: o pipeline antigo via ESP32 + micro-ROS Agent (usado em
> testes anteriores) está **deprecado** e foi removido desta estratégia. Hoje
> `encoder_guidao_node` lê a serial normal e cria seu próprio nó ROS2 no
> computador — não há mais `micro_ros_agent` nem `/dev/ttyUSB0` em lugar nenhum.

> 🪟 **Rodando no Windows?** Este README cobre o cenário Linux nativo. Para
> Docker Desktop + WSL2, use `docker-compose.windows.yml` e siga o guia
> dedicado em [`WINDOWS.md`](./WINDOWS.md) (rede bridge em vez de host, e
> `usbipd-win` para expor a porta serial do Arduino).

## O que eu encontrei no repositório (relevante para a estratégia)

- Workspace `colcon` com 10 pacotes em `src/`, todos `ament_python` exceto
  `hal_interfaces` e `thesis_interfaces` (mensagens/serviços custom, `ament_cmake`).
- **`build/`, `install/` e `log/` estão versionados no Git** — isso é resíduo de build
  do host do autor e **não deve ir para a imagem** (por isso o `.dockerignore` os exclui;
  o workspace é recompilado do zero dentro do container).
- `ros_tcp_endpoint` **não está em `src/`** — é dependência externa que o autor
  roda localmente após instalação manual. No Docker, eu a trago via build
  (clone + compilação), conforme o `INSTRUCTIONS` do próprio repo.
- `encoder_guidao_node` importa `serial` (pyserial) diretamente mas **não declara
  essa dependência no `package.xml`** — compensei instalando `python3-serial` no
  Dockerfile.
- O pipeline via ESP32 + `micro_ros_agent` (Terminal 1 do `INSTRUCTIONS` original)
  está deprecado e **não faz mais parte da stack** — era usado apenas em testes
  antigos.
- Não há `launch.py`: o fluxo oficial (arquivo `INSTRUCTIONS`) é subir os nós em
  terminais separados. Modelei isso como **5 serviços no `docker-compose.yml`**
  (um container por nó), o que também facilita restart/isolamento individual.
- `firmware/` é um projeto PlatformIO separado (ESP32) — **não faz parte do
  workspace ROS2** e não deve ser dockerizado junto; ele é flasheado à parte.

## Arquivos entregues

```
.
├── Dockerfile                    # build multi-stage
├── entrypoint.sh                 # sourcea os workspaces antes do CMD
├── docker-compose.yml            # 6 serviços (um por nó)
├── .dockerignore                 # exclui build/ install/ log/ firmware/
└── udev/99-rehab-platform.rules  # (opcional) symlinks fixos p/ portas seriais
```

Coloque `Dockerfile`, `entrypoint.sh`, `docker-compose.yml` e `.dockerignore` na
**raiz do repositório** (mesmo nível da pasta `src/`).

## Passo a passo

### 1. Pré-requisitos no host (Ubuntu 22.04)

```bash
# Docker + plugin compose
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER   # relogar depois disso
```

### 2. (Opcional, mas recomendado) fixar os nomes das portas seriais

Isso evita que `/dev/ttyACM0` troque de nome dependendo da ordem em que você
pluga os cabos (relevante se houver mais de um dispositivo serial USB na bancada).

```bash
sudo cp udev/99-rehab-platform.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Se pular esse passo, sem problema — só confirme com `ls /dev/tty*` quais nomes
estão em uso antes de subir os containers.

### 3. Firmware do Arduino "Guidao" (fora do Docker)

O `firmware/` é compilado/flasheado separadamente (fora do workspace ROS2),
como já é feito hoje. Depois de flasheado e conectado via USB, o Arduino
aparece como `/dev/ttyACM0`.

> O antigo passo de flashear o ESP32 para o pipeline micro-ROS não é mais
> necessário — esse fluxo está deprecado.

### 4. Build da imagem

⚠️ Com 5 serviços apontando pra mesma imagem no compose, **não use
`docker compose build`** nem `docker compose up --build` — isso dispara um
build paralelo por serviço, e todos tentam taguear a mesma imagem ao mesmo
tempo, o que quebra com `failed to solve: image ... already exists`.

Na raiz do repositório (onde você colocou os arquivos deste pacote), builde
a imagem **uma vez**, direto com `docker build`:

```bash
docker build -t rehab-platform:humble .
```

Isso vai:
- clonar e compilar o `ros_tcp_endpoint` junto com os pacotes do projeto (stage `workspace_builder`);
- montar a imagem final `rehab-platform:humble`.

Builds seguintes reaproveitam cache de camada e são rápidas.

### 5. Subir a stack

```bash
docker compose up
```

(sem `--build` — a imagem já foi construída no passo anterior)

Isso equivale aos terminais do `INSTRUCTIONS` original que continuam ativos hoje,
um por container (o antigo Terminal 1, do micro-ROS Agent com o ESP32, foi removido):

| Serviço              | Equivale a                         | Dispositivo mapeado |
|-----------------------|-------------------------------------|----------------------|
| `device_manager`      | hal_device_manager                  | —                    |
| `signal_processing`   | hal_signal_processing               | —                    |
| `tcp_endpoint`        | ros_tcp_endpoint (Unity)            | porta `10000`        |
| `guidao_encoder`      | encoder_guidao (lê serial direto)   | `/dev/ttyACM0`       |
| `guidao_controller`   | controller_guidao                   | —                    |

Para rodar em background: `docker compose up -d`
Para ver logs de um nó específico: `docker compose logs -f tcp_endpoint`
Para reiniciar só um nó: `docker compose restart guidao_encoder`

### 6. Configurar e abrir a Unity

Sem alterações em relação ao fluxo original — a Unity roda **fora** do Docker:

1. Menu **Robotics → ROS Settings** → `ROS IP Address` = IP da máquina Linux
   (a mesma que já usava, ex. `192.168.15.135`), porta `10000`.
2. Confirme que o `HalLeverVisualizer` está com o tópico `/hal/device_state`
   e `target ID = as5600_encoder`.
3. Play na Unity.

Como usamos `network_mode: host`, a porta `10000` do container `tcp_endpoint`
já é a porta `10000` da própria máquina Linux — nenhum mapeamento adicional é
necessário para a Unity enxergar o endpoint.

### 7. Validar que está tudo funcionando

- `docker compose logs tcp_endpoint` → deve mostrar handshake de conexão da Unity.
- No console da Unity → `"Subscribed to /hal/device_state..."` sem exceptions de socket.
- Mover o encoder físico → a alavanca 3D na Unity deve responder em tempo real.
- `docker compose exec device_manager ros2 topic list` → confirma que os tópicos
  dos outros containers aparecem (prova de que a descoberta DDS via host network
  está funcionando entre containers).

## Por que `network_mode: host`?

O DDS (RTPS/FastDDS) usado pelo ROS2 depende de multicast e de um range de portas
efêmeras para descoberta entre nós — mapear isso em modo bridge exigiria expor
dezenas de portas UDP dinâmicas. Rodando em `host`, cada container enxerga a
rede da máquina como se fosse um processo nativo, o que resolve tanto:
- a descoberta entre os 6 containers ROS2 entre si, quanto
- a Unity (fora do Docker) alcançando a porta `10000` do `tcp_endpoint`.

Isso só funciona em **Linux**. Se algum dia for rodar em Mac/Windows, será
necessário migrar para bridge networking + expor portas específicas, e possivelmente
usar `ROS_DOMAIN_ID`/discovery server para a parte DDS.

## Troubleshooting comum

**`Permission denied` no `/dev/ttyACM0`**
O usuário `rosdev` da imagem já está no grupo `dialout`, mas confirme que o
dispositivo no host também está acessível: `ls -l /dev/ttyACM0` deve mostrar
grupo `dialout`. Se necessário, `sudo usermod -aG dialout $USER` no host.

**`rosdep install` falha por falta de chave/rede durante o build**
O build do Dockerfile precisa de acesso à internet (clona `ROS-TCP-Endpoint` e
os índices do `rosdep`). Rode o build com a rede liberada; não há dependência
de internet em tempo de execução.

**`failed to solve: image "docker.io/library/rehab-platform:humble": already exists`**
Isso acontece se você usar `docker compose build` ou `docker compose up --build`
com os 5 serviços apontando pra mesma imagem — o Compose builda todos em
paralelo e eles competem pra taguear a mesma imagem. Solução: builde manualmente
com `docker build -t rehab-platform:humble .` (uma vez só) e depois suba com
`docker compose up`, sem `--build`.

**Containers não se enxergam / tópicos não aparecem entre eles**
Confirme que todos usam o mesmo `ROS_DOMAIN_ID` (já fixado em `0` no compose) e
que estão todos com `network_mode: host` — misturar host com bridge quebra a
descoberta DDS.