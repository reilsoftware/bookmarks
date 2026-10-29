FROM python:3.13-alpine

WORKDIR /app
COPY bm.py server.py template.html default.json ./

# Bookmarks, icon cache and generated site live in the /data volume.
ENV BOOKMARKS_HOME=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765

CMD ["python3", "server.py", "--host", "0.0.0.0", "--port", "8765"]
