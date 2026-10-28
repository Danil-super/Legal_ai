FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/services/gateway/telegram/src:/app/services/legal_core/src

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY services/gateway/telegram/src ./services/gateway/telegram/src
COPY services/legal_core/src/legal_core/__init__.py ./services/legal_core/src/legal_core/__init__.py
COPY services/legal_core/src/legal_core/pseudonymization.py ./services/legal_core/src/legal_core/pseudonymization.py
COPY services/legal_core/src/legal_core/contracts.py ./services/legal_core/src/legal_core/contracts.py
COPY services/legal_core/src/legal_core/case_material_contracts.py ./services/legal_core/src/legal_core/case_material_contracts.py
COPY services/legal_core/src/legal_core/analysis_clarifications.py ./services/legal_core/src/legal_core/analysis_clarifications.py
COPY services/legal_core/src/legal_core/report_presentation.py ./services/legal_core/src/legal_core/report_presentation.py
COPY services/legal_core/src/legal_core/upload_filename.py ./services/legal_core/src/legal_core/upload_filename.py
COPY services/legal_core/src/legal_core/factual_safety_intake.py ./services/legal_core/src/legal_core/factual_safety_intake.py
COPY services/legal_core/src/legal_core/case_preparation_contracts.py ./services/legal_core/src/legal_core/case_preparation_contracts.py
COPY services/legal_core/src/legal_core/intake_money.py ./services/legal_core/src/legal_core/intake_money.py
COPY services/legal_core/src/legal_core/intake_preparation_contracts.py ./services/legal_core/src/legal_core/intake_preparation_contracts.py
COPY services/legal_core/src/legal_core/intake_answer_contracts.py ./services/legal_core/src/legal_core/intake_answer_contracts.py
COPY services/legal_core/src/legal_core/intake_answer_intent.py ./services/legal_core/src/legal_core/intake_answer_intent.py
COPY services/legal_core/src/legal_core/intake_answer_wire.py ./services/legal_core/src/legal_core/intake_answer_wire.py
COPY services/legal_core/src/legal_core/intake_answer_values.py ./services/legal_core/src/legal_core/intake_answer_values.py
COPY services/legal_core/src/legal_core/intake_factual_candidates.py ./services/legal_core/src/legal_core/intake_factual_candidates.py
COPY services/legal_core/src/legal_core/intake_service_scope.py ./services/legal_core/src/legal_core/intake_service_scope.py
COPY services/legal_core/src/legal_core/analysis_contracts.py ./services/legal_core/src/legal_core/analysis_contracts.py
COPY services/legal_core/src/legal_core/reference_analogy_contracts.py ./services/legal_core/src/legal_core/reference_analogy_contracts.py
COPY services/legal_core/src/legal_core/lawyer_work_product_contracts.py ./services/legal_core/src/legal_core/lawyer_work_product_contracts.py
COPY services/legal_core/src/legal_core/risk_engine.py ./services/legal_core/src/legal_core/risk_engine.py
COPY services/legal_core/src/legal_core/factual_safety_risk.py ./services/legal_core/src/legal_core/factual_safety_risk.py

USER app

# Import the real composed entry point without starting polling or requiring credentials.
# A green source-tree test suite does not prove that COPY included every shared dependency.
RUN python -c "import telegram_gateway.__main__, telegram_gateway.analysis_jobs_runtime"
RUN python -c "import telegram_gateway.lawyer_work_product_runtime"

CMD ["python", "-m", "telegram_gateway"]
