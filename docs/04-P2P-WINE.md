# 04 - Intelbras P2P, Wine e Distribuição

## CamAccess

O core de IA não conhece a origem do frame. `CamAccess` normaliza:

- RTSP direto.
- Intelbras P2P.
- edge push futuro.

## P2P Session Manager

Responsável por:

- serial/dispositivo.
- login seguro.
- abertura de sessão.
- descoberta/reserva de portas locais.
- health check.
- reconnect/backoff.
- encerramento limpo.

Credenciais ficam fora do Git.

## Port Registry

Nenhum processo pode escolher porta livre por tentativa aleatória. O registry deve reservar atomicamente:

- porta SDK/TCP.
- porta RTSP.
- owner/Wine.
- DVR.
- tempo de lease.

Isso impede colisões em restart/failover.

## Wine Supervisor

Métricas por processo:

- `wine_id`.
- PID.
- DVR associado.
- sessões.
- câmeras ativas.
- CPU.
- RAM.
- FPS.
- frame gaps.
- latência P2P.
- reconexões.
- último erro.
- uptime.

## Scheduler

A distribuição não é apenas número de câmeras. Um `load_score` deve considerar pelo menos:

- câmeras ativas.
- bitrate/frames.
- CPU decode.
- RAM.
- latência P2P.
- relay/direct quando disponível.
- reconnection rate.
- taxa de eventos.
- peso do DVR.

A migração automática precisa ser transacional: reservar destino, validar, comutar e só depois liberar origem.

## Botão de emergência

A interface oferece, por DVR:

- Testar DVR.
- Trocar túnel.
- Reiniciar Wine.
- Migrar para outro Wine.

Ações destrutivas exigem confirmação e geram audit log.

## Dependência proprietária

DLLs, executáveis, SDKs e binários Intelbras necessários ao bridge P2P não são armazenados neste repositório. Devem ser implantados a partir de fonte autorizada e versionados em inventário interno com checksum.

## Bridge QR/T2U já instalado

Quando o gateway autorizado estiver em /opt/vision-ia/qr-gateway, o instalador detecta o SDK Linux, os arquivos de status e o mapa de DVRs. Ele inicia dois serviços internos:

- t2u-status-sync: registra somente túneis que o bridge reporta como conectados e mostra a porta SDK real; não inventa porta RTSP.
- t2u-capture: abre uma sessão curta pelo SDK oficial para a câmera selecionada, decodifica o stream DHAV e entrega um JPEG real ao portal administrativo.

O acesso ao mapa e às credenciais do gateway é somente leitura e fica restrito ao contêiner de captura. Se o SDK não fornecer frame, a câmera permanece sem vídeo validado.

