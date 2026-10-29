"""Explicit UNKNOWN attempts for synthetic, completed-summary test fixtures."""

from copy import deepcopy


def with_unknown_clarification_attempts(data):
    """A completed-summary fixture includes authored attempts, without invented facts."""
    result = deepcopy(data)
    answers = result.setdefault("analysisClarificationAnswers", {"answers": []})["answers"]
    attempted = {(item["questionId"], item.get("serviceSlot")) for item in answers}
    requests = [("RESPONSE_PURPOSE", None)]
    if "TREATMENT" in result.get("situationAreas", []):
        requests.extend((question, slot)
                        for slot in range(len(result.get("affectedServices", [])))
                        for question in ("SERVICE_PAYMENT_BASIS", "SERVICE_PROVIDED_DATE"))
    for question, slot in requests:
        if (question, slot) not in attempted:
            answers.append({"questionId": question, "serviceSlot": slot,
                            "value": {"date": None, "precision": "UNKNOWN"}
                            if question == "SERVICE_PROVIDED_DATE" else "UNKNOWN"})
    return result

