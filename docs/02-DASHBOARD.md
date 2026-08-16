# 02 - Dashboard Operacional

## Cards principais

- Câmeras online.
- Câmeras offline.
- Câmeras degradadas.
- Túneis P2P ativos.
- Logs em tempo real.
- Logs aguardando verificação IA.
- Logs aprovados.
- Logs rejeitados.
- Logs em revisão humana.
- Eventos em certificação.

## Infraestrutura

O dashboard deve exibir em tempo real:

- CPU do host.
- RAM usada/total.
- GPU utilization.
- VRAM usada/total.
- temperatura da GPU.
- filas Redis.
- backlog do reviewer IA.
- latência de detecção.
- FPS efetivo por câmera.
- armazenamento PostgreSQL/MinIO.

## Condomínios

Tabela resumida:

`Condomínio | Online | Offline | Degradadas | Total | Eventos hoje | Revisão IA`

Expansão hierárquica:

`Condomínio -> DVR -> Câmera`

## Túneis P2P

Cada DVR mostra:

- status P2P.
- Wine atual.
- porta SDK/TCP local ativa.
- porta RTSP local ativa.
- horário de início da sessão.
- latência.
- reconexões.
- último erro SDK.
- botão `+` para histórico de túneis.
- botão de troca emergencial.

### Histórico expansível

Cada registro contém:

- início e fim.
- portas antigas.
- Wine antigo.
- motivo da troca.
- ator: usuário, IA ou watchdog.
- erro associado.

### Troca emergencial

A troca segue processo controlado:

1. Diagnosticar sessão atual.
2. Reservar nova porta sem colisão.
3. Abrir nova sessão quando o SDK permitir.
4. Validar autenticação.
5. Validar stream e múltiplos frames.
6. Redirecionar Stream Broker.
7. Confirmar heartbeat.
8. Encerrar sessão anterior.
9. Registrar auditoria/histórico.

## Logs

Filtros:

- condomínio.
- DVR.
- câmera.
- evento.
- período.
- confiança.
- versão da regra.
- status de certificação.
- status AI review.

Ações:

- Visualizar snapshot.
- Visualizar mini-clipe.
- Download da evidência.
- Aprovar.
- Rejeitar.
- Marcar inconclusivo.
- Enviar feedback para dataset.
