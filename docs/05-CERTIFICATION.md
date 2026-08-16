# 05 - Certificação de Eventos

## Unidade de certificação

A certificação pertence a:

`condominium_id + dvr_id + camera_id + event_type + rule_version`

Uma mesma regra pode estar certificada em uma câmera e em homologação em outra.

## Estados

1. `DRAFT`
2. `CONFIGURED`
3. `SHADOW`
4. `HOMOLOGATION`
5. `AI_REVIEW`
6. `CERTIFIED`
7. `PRODUCTION`
8. `REJECTED`
9. `ADJUSTMENT_REQUIRED`

Qualquer alteração material em ROI, geometria, modelo, limiar ou máquina temporal cria nova versão e volta para homologação.

## Pipeline

`Detection Candidate -> Rule Engine -> Certification -> AI Reviewer -> Human Review when needed -> Production Log`

## Filas separadas

- candidatos técnicos.
- revisão IA.
- aprovados.
- rejeitados.
- inconclusivos.
- revisão humana.
- amostras de certificação.

Logs de homologação nunca aparecem como evento válido do cliente.

## Métricas por regra

- amostras positivas.
- amostras negativas.
- true positives.
- false positives.
- false negatives.
- precision.
- recall.
- false-positive rate.
- versão do detector.
- versão do reviewer.
- versão da regra.

## Aprendizado operacional

Feedback permitido:

- correto.
- falso positivo.
- falso negativo.
- inconclusivo.

O histórico alimenta RAG, ajustes de regra, conjuntos de avaliação e datasets de treinamento futuro. Pesos de modelos não se autoalteram em produção.
