FROM python:3.10-slim

ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ENABLECORS=false

WORKDIR /app
COPY planning_gardes_app.py .
RUN pip install --no-cache-dir streamlit pandas
EXPOSE 8501
CMD ["streamlit", "run", "planning_gardes_app.py"]
