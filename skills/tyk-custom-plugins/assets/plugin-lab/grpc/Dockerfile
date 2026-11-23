FROM python:3.12-slim
WORKDIR /srv
RUN pip install --no-cache-dir grpcio==1.76.0 grpcio-tools==1.76.0
COPY *.proto ./
RUN python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. *.proto
COPY server.py ./
CMD ["python", "-u", "server.py"]
