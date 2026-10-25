# Gráfico local da telemetria do controle

O script `plot_control_telemetry.py` roda no computador host (fora do Docker) e
mostra em tempo real o foco calibrado recebido (`normalized_value`), o fator de
modulação calculado, o controle após a modulação e o controle ideal antes dela.
Ele recebe os dados por UDP na porta
8765; o controlador envia esses pacotes quando `telemetry_udp_host` está
configurado.

No Windows, instale Matplotlib no Python local e inicie o gráfico:

```powershell
py -m pip install matplotlib
py scripts/plot_control_telemetry.py
```

Depois (ou antes) inicie os serviços com `docker-compose.windows.yml`. O serviço
`guidao_controller` encaminha os dados para `host.docker.internal:8765`; se ele
já estava ativo ao configurar isso, recrie-o:

```powershell
docker compose -f docker-compose.windows.yml up -d --force-recreate guidao_controller
```

O eixo horizontal mantém os últimos 30 segundos. Para alterar janela e porta,
use `--window 60` ou `--port 8766`; se alterar a porta, ajuste também
`telemetry_udp_port` no serviço do controlador.
