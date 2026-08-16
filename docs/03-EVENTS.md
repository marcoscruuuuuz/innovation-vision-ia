# 03 - Eventos e Editor de Regras

## Modelos para tráfego

- **Veículo na contramão:** PP-Vehicle/PP-Tracking com segmentação de faixa PP-LiteSeg. A direção é comparada ao vetor de faixa desenhado e calibrado na ROI.
- **Placa do veículo:** detector de placa PP-Vehicle e reconhecedor de placa PP-OCRv3, com voto temporal entre frames.

Esses modelos são requisitos de configuração, não uma alegação de operação: sem adaptador especializado, pesos, câmera e frames reais, o worker deve manter a regra bloqueada como `MODEL_REQUIRED`.

## Editor por câmera

Fluxo de seleção:

`Condomínio -> DVR -> Câmera`

A tela de configuração abre somente uma câmera por vez no stream principal/resolução máxima e oferece:

- câmera anterior.
- próxima câmera.
- pausar.
- snapshot.
- zoom.
- limpar desenho.

Ao sair do editor, a câmera volta ao stream operacional.

## Geometrias

Ferramentas:

- retângulo.
- polígono.
- linha.
- linha dupla.
- linha de disparo.
- porta: ROI + linha auxiliar automática.

Coordenadas devem ser armazenadas normalizadas (0..1) para não depender da resolução usada no editor.

## Nome opcional de saída

Toda regra possui `display_label` opcional.

Exemplo:

- `event_type`: `porta_aberta_bloco`
- `display_label`: `BLOCO 01`
- saída: `porta_aberta_(BLOCO 01)`

O nome técnico nunca é alterado pelo label, preservando pesquisas e métricas.

## Setores / presets

### BLOCOS
- `porta_aberta_bloco`
- `area_janelas_apartamentos`
- `linha_perimetral`
- `area_interesse`
- `cachorro_solto`
- `cachorro_fazendo_fezes`

### PORTAO_ENTRADA_SAIDA
- `entrada_vacuo`
- `saida_vacuo`
- `pessoa_portao_veicular`

### AREA_COMUM
- `crianca_bicicleta_area_comum`
- `crianca_correndo_area_comum`
- `veiculo_area_proibida`
- `veiculo_contramao`
- `movimentacao_apos_22h`
- `bola_fora_quadra`
- `crianca_soltando_pipa`
- `placa_detectada`
- `cachorro_solto`
- `cachorro_fazendo_fezes`

### PORTARIA
- `face_detectada`
- `porteiro_dormindo`
- `porteiro_fora_posto`

### LIXEIRA
- `lixo_no_chao`

## Regras especiais

### Porta aberta

1. Operador desenha ROI da porta.
2. Sistema cria linha auxiliar.
3. Operador seleciona lado esquerdo/direito.
4. Sistema calibra estado fechado.
5. Mudança estrutural indica abertura.
6. Se permanecer aberta por 15 segundos, nasce candidato a evento.
7. Durante homologação, candidato não vira log válido de cliente.

### Contramão

Não é um classificador isolado. Deve usar:

`detector de veículo + tracking + ROI da pista + vetor permitido + trajetória + persistência`

### Placa

Pipeline separado:

`vehicle detector -> plate detector -> crop/perspective -> OCR -> temporal voting -> validation`

### Criança

Eventos que afirmam criança precisam de classificação `adult/child/unknown`. Em baixa confiança, o resultado vai para revisão e não acusa automaticamente.

### Movimento após 22h

Combina:

- motion/MOG2.
- scene change.
- detector de objetos.
- mudança estrutural de porta/janela.
- janela de horário configurável.

### Passagem no vácuo

Preferência: correlacionar vídeo com evento de acesso/QR/controladora quando a integração existir. Sem integração externa, usar linhas + tracking + janela temporal.
