FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python scripts/generate_synthetic_data.py --out data/public
ENV TANRIC_STORAGE=local TANRIC_DATA_ROOT=data
EXPOSE 8000
CMD ["uvicorn", "tanric.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
