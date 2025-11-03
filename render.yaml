services:
  - type: web
    name: kakeru-bot
    env: python
    plan: free
    buildCommand: "pip install -r requirements.txt"
    startCommand: "gunicorn main:app"
    envVars:
      - key: PYTHON_VERSION
        value: 3.10.12
      - key: DATABASE_URL
        sync: false
      - key: LINE_CHANNEL_ACCESS_TOKEN
        sync: false
      - key: LINE_CHANNEL_SECRET
        sync: false
      - key: OPENAI_API_KEY
        sync: false
      - key: CRON_TOKEN
        sync: false
