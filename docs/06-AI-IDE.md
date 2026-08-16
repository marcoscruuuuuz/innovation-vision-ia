# 06 - ISABEL Vision IDE

## Papel

A ISABEL é o agente administrativo/IDE interno do VISION IA. Ela interpreta comandos em linguagem natural e coordena ferramentas autorizadas.

Exemplos:

- verificar túneis instáveis de um condomínio.
- diagnosticar câmera sem frames.
- verificar distribuição de DVRs entre Wines.
- simular rebalanceamento.
- analisar falsos positivos de uma regra.
- preparar alteração de ROI/parâmetros.
- consultar uso de GPU/RAM.

## Tool Gateway

Ferramentas mínimas:

```text
p2p.list_sessions
p2p.test_dvr
p2p.reconnect
p2p.get_latency
p2p.rotate_tunnel

wine.list
wine.health
wine.restart
wine.migrate_dvr

camera.get
camera.health
camera.snapshot
camera.test_stream

event.get_rule
event.simulate
event.create_version
event.enable
event.disable

certification.start
certification.review
certification.compare
certification.approve

system.health
system.cpu
system.ram
system.gpu
system.storage
```

## Permissões

- `READ`: consultar e diagnosticar.
- `PLAN`: preparar/simular mudanças.
- `EXECUTE`: aplicar mudanças explicitamente autorizadas.

A IA não recebe shell root livre.

## Reviewer multimodal

O Event Reviewer é separado da IDE. Ele pode receber snapshot/clipe e contexto da regra para segunda opinião durante homologação/revisão. A decisão final continua registrada, versionada e auditável.

## GPU

A IDE e o reviewer trabalham com filas e prioridade inferior à detecção de produção.
