# O framework: o que uma SRE mede quando a saída é probabilística

Um slide. A prática clássica de SRE diz: escolha poucos SLIs voltados ao usuário, defina
objetivos, alerte pela velocidade com que o error budget está sendo consumido. Um endpoint de
LLM não muda isso; muda *o que* é voltado ao usuário. Quem usa um modelo com streaming sente
quatro coisas: se a resposta veio, quanto demorou para começar, com que fluidez chegou e se
era boa. Custo é o que o negócio sente.

| SLI | Por que importa para o usuário | Como é medido aqui | Projeto CNCF responsável |
|---|---|---|---|
| **Disponibilidade** — proporção de respostas boas, objetivo 99 % | Um 200 com resposta vazia ou cortada é falha para o usuário, não sucesso. Erros, timeouts, respostas vazias e truncadas são eventos ruins. | O gateway classifica cada requisição (`llm_slo.requests` por `llm_slo.outcome`). | OpenTelemetry (contador) → Prometheus (razão por janela) |
| **Responsividade** — tempo até o primeiro token, objetivo 95 % < 2 s em CPU | O TTFT é o momento em que o usuário vê que o modelo está vivo. É o sinal de fila: cresce quando requisições esperam por uma réplica ocupada. | Histograma `gen_ai.server.time_to_first_token` (semconv GenAI), bom = bucket `le="2"`. | OpenTelemetry (histograma semconv) → Prometheus (`histogram_quantile`, razão de buckets) |
| **Vazão** — tokens de saída por segundo por requisição, objetivo 90 % > 3 tok/s | Velocidade de leitura. Abaixo de ~3 tokens/s o stream parece quebrado mesmo terminando. | O gateway mede tokens após o primeiro (`llm_slo.output_tokens`, unidade `{token}/s`); tokens contados com o tokenizer do modelo quando o servidor não envia `usage`. | OpenTelemetry → Prometheus; modelo servido pelo KServe |
| **Qualidade** — verificações de avaliação passando, objetivo 90 % | A saída é probabilística; o único sinal honesto é um teste repetível. Verificações determinísticas pegam regressões (prompt ruim, rollout ruim, quantização ruim). | Um CronJob roda um conjunto dourado com decodificação gulosa e emite `llm_slo.eval.checks` pass/fail via OTLP. | Kubernetes CronJob → OpenTelemetry → Prometheus |
| **Custo** — custo nocional por 1k requisições, um orçamento, não um SLO | Tokens e horas de nó são dinheiro; respostas lentas custam mais tempo de nó. | O gateway calcula custo amortizado de nó e por token em cada requisição (`llm_slo.request.cost`, premissas em um ConfigMap). | OpenTelemetry → Prometheus (alerta de ticket, sem page) |

Como o ciclo se fecha:

- **Alertas**: regras multi-janela e multi-burn-rate (Google SRE Workbook): page em 1h/5m a
  14,4× ou 6h/30m a 6×, ticket em 1d/2h a 3× ou 3d/6h a 1×. Para objetivos de 90 % os fatores
  de page são limitados a 8×/5×, porque uma burn rate não pode passar de 1/(1 − objetivo).
  Geradas de uma única especificação (`slo/slos.yaml`), testadas com `promtool test rules`.
  **Prometheus + Alertmanager.**
- **Capacidade**: o predictor escala por profundidade de fila (requisições em voo por
  réplica), não por CPU, porque um modelo limitado por CPU está sempre a 100 % de CPU quando
  trabalha. **KEDA**, pela integração nativa do KServe.
- **Serving**: um modelo aberto e pequeno atrás de um endpoint compatível com OpenAI, com
  streaming. **KServe.**
- **Tracing**: um span por requisição com os atributos GenAI (modelo, tokens, motivo de
  término, resultado), sem conteúdo de prompt a menos que habilitado. **OpenTelemetry → Jaeger.**
- **Dashboards**: cada SLI contra seu objetivo, error budget, burn rate, heatmap de TTFT,
  tokens/s, custo, taxa de aprovação da avaliação, réplicas vs. em voo, alertas disparando.
  **Grafana.**

Três coisas que o laboratório ensinou e o slide não consegue:

1. **A especificação se move debaixo de você.** As convenções semânticas GenAI ainda estão em
   "Development" e foram movidas para um novo repositório no meio do laboratório, com métricas
   renomeadas na `main`. Mantenha todos os nomes em um módulo e teste-os contra o pacote.
2. **"Temperature 0" não é promessa.** O backend de serving tratou 0 como "não informado" e a
   configuração de sampling do próprio modelo valeu. Foi o sinal de avaliação que pegou isso.
3. **Tráfego sintético é tráfego.** As execuções do avaliador contam como carga para o
   autoscaler e competem com usuários em um modelo que atende uma requisição por vez.
   Marque-o, exclua-o dos SLIs de usuário, mantenha-o curto.
