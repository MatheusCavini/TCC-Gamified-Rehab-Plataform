# Banco de dados local

O PostgreSQL roda em um container Docker e persiste os dados no volume nomeado
`postgres_data`. A porta fica publicada somente em `127.0.0.1`, portanto o
banco não é exposto diretamente à rede.

## Preparar

1. Se ainda não existir `.env`, copie `.env.example` para `.env` e defina uma
   senha local forte em `POSTGRES_PASSWORD`. O `.env` local criado nesta
   configuração já tem uma senha aleatória e é ignorado pelo Git.
2. Recompile a imagem ROS 2 para incluir o novo nó de persistência:

   ```powershell
   docker build -t rehab-platform:humble .
   ```

3. Inicie o banco e o nó ROS de persistência:

   ```powershell
   docker compose -f docker-compose.windows.yml up -d postgres data_store
   ```

   No Linux, use `docker compose up -d postgres data_store`.
4. Confirme que ambos estão ativos:

   ```powershell
   docker compose -f docker-compose.windows.yml ps postgres data_store
   ```

Conexão a partir do computador: host `localhost`, porta `5432`, banco `rehab`,
usuário `rehab`. Na rede Docker do Compose Windows, os containers podem usar
`postgres:5432`. O schema inicial é criado em `database/init.sql` apenas
quando o volume é novo.

## Modelo inicial

- `patients`: identificadores UUID fictícios para o protótipo.
- `sessions`: uma sessão vinculada a um paciente.
- `sessions.metadata`: identificação do jogo, estado final e configurações do exercício.
- `signal_files`: metadados e caminho para arquivos brutos ou filtrados.
- `performance_metrics`: métricas vinculadas à sessão.

Os sinais são arquivos, não ficam embutidos no PostgreSQL. Coloque-os em
`data/sessions/`; essa pasta é ignorada pelo Git. O nó `data_store` grava os
arquivos brutos e filtrados por sessão; o banco guarda caminhos relativos e
metadados em `signal_files`. O nó também grava valores de RMS, normalização e
métricas publicadas em `/hal/device_state` na tabela `performance_metrics`.
As medições numéricas publicadas pela Unity em `/game/performance_metrics`
também são registradas com nomes `game.*` e horário de recepção. `game_id`,
`finished` e configurações pulmonares ficam em `sessions.metadata`; essas
configurações não são repetidas como amostras da série temporal.

Se o volume PostgreSQL já existia antes da adição de `sessions.metadata`, aplique
a migração uma vez no PowerShell:

```powershell
Get-Content database/migrations/002_session_metadata.sql | docker exec -i rehab_postgres psql -U rehab -d rehab
```

## Iniciar e encerrar uma gravação

No PowerShell, abra um shell no container:

```powershell
docker exec -it rehab_data_store bash
```

Dentro dele, carregue o ambiente ROS e inicie a gravação com o UUID fictício
do paciente:

```bash
source /opt/ros/humble/setup.bash
source /ros2_ws/install/setup.bash
ros2 service call /recording/start hal_interfaces/srv/SetString "{data: '11111111-1111-4111-8111-111111111111'}"
```

O serviço cria o paciente fictício se ele ainda não existir e devolve o UUID da
sessão. O nó grava enquanto a sessão estiver ativa. Para encerrá-la ao voltar
ao menu:

```bash
ros2 service call /recording/stop std_srvs/srv/Trigger "{}"
```

O serviço de início também aceita JSON com `patient_id`, `protocol` e `device`.
Na integração com a Unity, a gravação começa quando o exercício inicia e termina
quando a sessão retorna ao menu.

## Backup e limpeza

Crie um dump local com:

```powershell
New-Item -ItemType Directory -Force data/backups | Out-Null
docker exec rehab_postgres pg_dump -U rehab -Fc rehab -f /tmp/rehab.dump
docker cp rehab_postgres:/tmp/rehab.dump data/backups/rehab.dump
```

Copie também `data/sessions/` para o backup: o dump contém metadados, não os
arquivos de biossinais. Para parar sem apagar os dados, use
`docker compose -f docker-compose.windows.yml down`. **Não use `down -v`** a
menos que queira apagar o banco permanentemente.

Este ambiente é para prototipação com dados sintéticos. O `.env`, o volume
Docker, os arquivos de sinais e os backups não devem ser enviados ao Git.

O armazenamento automático de arquivos cobre atualmente os streams EMG
anunciados em `/devices/available`; o sinal filtrado é emitido pelo
`signal_processing_node`. Streams sem taxa de amostragem válida são ignorados
para evitar metadados incorretos. Métricas EEG publicadas pelo processamento
são armazenadas como resultados processados.
