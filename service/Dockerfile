FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Generate the hash-locked export and wheel with the commands in deployment/README.md.
COPY .build/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /build/requirements.txt
COPY .build/*.whl /build/
RUN pip install --no-cache-dir --no-deps /build/*.whl && rm -rf /build
USER 1000:1000
ENTRYPOINT ["python","-m","hmt_governance.server"]
