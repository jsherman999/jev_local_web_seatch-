OpenRouter gives models a common API, but it doesn’t make their supported features, access requirements, or reliability identical.

Think of three separate questions: **Can we call it? Can we interpret its response? Can it actually complete the task?**

| Layer | Errors we’ve seen | What they tell us |
|---|---|---|
| **Availability and access** | Muse required age confirmation; Llama Scout had no available endpoint. | Being listed in the catalog doesn’t guarantee the account can run the model. |
| **Supported API features** | Aion’s routes rejected forced tool selection. Haiku worked after switching to a strict JSON schema. | Different models and provider endpoints support different ways of requesting structured actions. |
| **Response formatting** | Haiku and Aion produced non-JSON replies. DeepSeek returned JSON that our action validator rejected. | The same instruction to “return JSON” is not equally reliable across models. Our parser was also overly strict about harmless formatting variations. |
| **Output budget** | Qwen exhausted its token allowance without returning action text. | Some models spend substantial output tokens on reasoning before producing a usable answer. |
| **Action correctness** | Nemo selected an unobserved target. | Valid JSON can still describe an invalid action. |
| **Task competence** | Nova followed irrelevant products; Phi entered a reviews/login path; Hermes repeated search actions. | A model can produce perfectly valid commands while pursuing an ineffective strategy. |
| **Our browser implementation** | Empty Amazon pages and overbroad freshness checks stopped runs. | Some failures came from our app, rather than the models. Those shared issues were fixed. |

**One calling function is feasible; one identical request configuration is less reliable.** A robust implementation usually has a shared caller with small adapters for:

- Structured JSON versus tool calls.
- Supported routing parameters.
- Reasoning settings and output limits.
- Response normalization and error interpretation.

After that, every model should pass through the **same execution safeguards and outcome checks**. We shouldn’t execute an invented target or declare success merely because a model says DONE.

There is also a benchmarking tradeoff:

- **Identical settings** measure how well each model works as a drop-in replacement.
- **Appropriate settings for each model** measure what each can achieve when integrated properly.

Our demo currently mixes those approaches somewhat: Haiku and Nemo have schema adaptations, while most others use generic JSON mode. That’s useful for compatibility testing, but should be disclosed when comparing results.

Jev’s typed decisions reduce the formatting problem. They don’t eliminate browser failures, wrong choices, or premature completion. Likewise, a failed run here doesn’t prove a general LLM cannot browse—it may need a better interface contract, a different budget, or a stronger workflow.
