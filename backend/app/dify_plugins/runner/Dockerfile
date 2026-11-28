FROM python:3.12-slim
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_CONFIG_FILE=/dev/null
WORKDIR /opt/harness
COPY requirements.harness.txt /opt/harness/requirements.txt
RUN python -m pip --isolated install --disable-pip-version-check --no-cache-dir --only-binary=:all: --index-url https://pypi.org/simple -r /opt/harness/requirements.txt
COPY worker.py /opt/harness/worker.py
COPY plugin/ /opt/plugin/
WORKDIR /opt/plugin
USER 65532:65532
ENTRYPOINT ["python", "-I", "/opt/harness/worker.py"]
