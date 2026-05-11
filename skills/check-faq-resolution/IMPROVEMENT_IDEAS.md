# FAQ Resolution Skill: Possible Improvements

The current skill is intentionally simple for teaching: it passes the ticket,
triage decision, and active FAQ table to an LLM, then asks for one structured
JSON decision. That makes the workflow easy to understand, but a production
system would usually add more controls.

## 1. Add Retrieval When the FAQ Table Grows

Passing every FAQ to the LLM is clear and works for a small classroom dataset.
If the FAQ table grows to hundreds or thousands of rows, add a retrieval step
that selects the most relevant candidates first. This could use embeddings,
keyword search, or both. The LLM would then judge only the top candidates.

## 2. Calibrate Confidence Scores

The model currently supplies a confidence value, and the workflow drafts an FAQ
response when confidence is at least `0.70`. A stronger system would compare
model confidence against historical outcomes and tune the threshold based on
false positives, false negatives, and the cost of unnecessary escalations.

## 3. Require Stronger Evidence

The model already returns ticket evidence and FAQ evidence. A production version
could enforce stricter checks, such as requiring the model to cite the exact
symptom and the exact FAQ solution step that resolves it.

## 4. Improve Missing-Information Handling

The skill asks whether the customer supplied the information required by the
matched FAQ. A next version could return a structured list of missing fields,
then send a clarification request instead of immediately escalating.

## 5. Add Human Review for Risky Cases

Low-confidence matches, security issues, billing disputes, executive accounts,
or tickets with high business impact should route to a human even when an FAQ
appears to match.

## 6. Track Model Versions and Prompts

The skill records the model in the decision summary, but it does not store the
full prompt or prompt version. A production audit trail should include the model,
prompt version, input hash, and response hash so decisions can be reproduced.

## 7. Protect Sensitive Data

Before sending tickets to an external model, the workflow could redact secrets,
access tokens, private customer data, or unnecessary personal information.

## 8. Build a Larger Evaluation Set

The repository includes a small evaluation script, but a real workflow should
use many historical tickets labeled by humans. The evaluation should separately
measure correct FAQ matches, correct no-match decisions, wrong FAQ selections,
and unnecessary escalations.

## 9. Add FAQ Learning with Human Approval

When the model chooses no match and a specialist later resolves the ticket, the
workflow could draft a new FAQ candidate. A human should approve that candidate
before it becomes active in the FAQ table.

## 10. Add Fallback Behavior

If the LLM call fails, the current skill returns an error. A production system
could automatically escalate the ticket, retry with another model, or fall back
to a simpler deterministic lookup depending on service-level requirements.
