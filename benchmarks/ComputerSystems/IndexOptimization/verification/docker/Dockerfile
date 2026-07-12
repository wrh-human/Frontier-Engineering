FROM postgres:16

# Python runtime for evaluator scripts
RUN apt-get update && apt-get install -y python3 python3-pip python3-psycopg2 && rm -rf /var/lib/apt/lists/*

# Default entry point: PostgreSQL
CMD ["postgres"]
