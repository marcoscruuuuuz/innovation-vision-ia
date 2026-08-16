# Status do Repositório

## Incluído

- Arquitetura consolidada do servidor único.
- Requisitos de dashboard.
- Catálogo inicial de eventos e geometrias.
- Regras de certificação e versionamento.
- Design P2P/Wine/Port Registry/failover.
- Design ISABEL Vision IDE/Tool Gateway.
- Schema PostgreSQL inicial.
- Docker Compose da infraestrutura base.
- Prometheus + Node Exporter + Grafana + DCGM opcional.
- Instalador Ubuntu único.
- Validador local/CI.

## Deliberadamente não incluído

- SDK/DLL/EXE Intelbras proprietário.
- credenciais reais.
- modelos treinados/weights.
- código final de cada microserviço.
- configuração Evolution API real.

## Definição de pronto desta fundação

Esta fundação está pronta quando:

1. validação shell/YAML/Compose passa;
2. nenhum segredo proibido está versionado;
3. repositório possui commit inicial limpo;
4. instalador consegue preparar Ubuntu suportado;
5. infraestrutura base sobe em host Ubuntu homologado.

A certificação de eventos de produção exige implementação dos serviços e testes com câmeras reais, não podendo ser inferida apenas pela validade desta estrutura.
