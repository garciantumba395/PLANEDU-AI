FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY planedu ./planedu
ENV PLANEDU_DATA=/tmp/planedu-data PLANEDU_DEMO=1 PLANEDU_DEMO_NO_OTP=1 PYTHONUNBUFFERED=1
CMD ["python", "-m", "planedu"]
