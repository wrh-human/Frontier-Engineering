#!/bin/bash
# Generate TPC-H SF1 data for IndexOptimization benchmark.
#
# Usage:
#   bash data/tpch_sf1/gen_data.sh
#
# This script:
# 1. Checks that Docker and the frontier-pg-index image are available
# 2. Clones dbgen and generates TPC-H SF1 data files
# 3. Starts a PostgreSQL container and loads the data
# 4. Creates a pg_dump file for fast evaluator restore
# 5. Cleans up

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DUMP_FILE="${SCRIPT_DIR}/tpch_sf1.dump"
SCHEMA_FILE="${SCRIPT_DIR}/schema.sql"
DBGEN_DIR="/tmp/tpch_dbgen_index_opt"

# Prerequisites
if ! command -v docker &>/dev/null; then
    echo "Error: docker is required but not found."
    exit 1
fi

if ! docker image inspect frontier-pg-index:latest &>/dev/null; then
    echo "Building frontier-pg-index:latest image..."
    REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
    docker build -t frontier-pg-index:latest "${REPO_ROOT}/verification/docker/"
fi

# Clone or update dbgen
if [ -d "${DBGEN_DIR}" ]; then
    echo "dbgen already exists at ${DBGEN_DIR}"
else
    echo "Downloading TPC-H dbgen..."
    if command -v git &>/dev/null; then
        git clone --depth 1 https://github.com/electrum/tpch-dbgen.git "${DBGEN_DIR}"
    else
        mkdir -p "${DBGEN_DIR}"
        cd "${DBGEN_DIR}" && curl -sL "https://github.com/electrum/tpch-dbgen/archive/refs/heads/master.tar.gz" | tar -xz --strip=1
    fi
fi

# Compile dbgen
echo "Compiling dbgen..."
cd "${DBGEN_DIR}"
if [ ! -f dbgen ]; then
    make -j4 2>&1 || { echo "dbgen compilation failed"; exit 1; }
fi

# Generate data
echo "Generating TPC-H SF1 data files..."
./dbgen -s 1 -f
echo "Data files: $(ls *.tbl | wc -l) tables"

# Start PostgreSQL container
echo "Starting PostgreSQL..."
CONTAINER_ID=$(docker run -d --rm \
    -e POSTGRES_PASSWORD=postgres \
    -v "${DBGEN_DIR}:/tpch_data" \
    -P frontier-pg-index:latest)

# Wait for PostgreSQL to be ready
for i in $(seq 1 30); do
    if docker exec "${CONTAINER_ID}" pg_isready -q 2>/dev/null; then
        echo "PostgreSQL ready after ${i}s"
        break
    fi
    sleep 1
    if [ "$i" -eq 30 ]; then
        echo "Error: PostgreSQL did not start within 30s"
        docker stop "${CONTAINER_ID}" >/dev/null
        exit 1
    fi
done

# Remove trailing pipe from .tbl files (TPC-H format compatibility)
for f in "${DBGEN_DIR}"/*.tbl; do
    sed -i.bak 's/|$//' "$f"
    rm -f "${f}.bak"
done

# Create schema
echo "Creating database schema..."
docker exec -i "${CONTAINER_ID}" psql -U postgres < "${SCHEMA_FILE}"

# Load data
echo "Loading TPC-H SF1 data (this may take a few minutes)..."
for tbl in nation region part supplier partsupp customer orders lineitem; do
    echo -n "  ${tbl}... "
    docker exec -i "${CONTAINER_ID}" psql -U postgres -c \
        "\\copy ${tbl} FROM '/tpch_data/${tbl}.tbl' WITH DELIMITER '|' NULL ''" \
        2>&1 | grep -c "^COPY" | xargs echo -n
    echo " rows loaded"
done

# Create dump
echo "Creating pg_dump archive..."
docker exec "${CONTAINER_ID}" pg_dump -U postgres -Fc -f /tmp/tpch_sf1.dump
docker cp "${CONTAINER_ID}:/tmp/tpch_sf1.dump" "${DUMP_FILE}"

# Stop container
docker stop "${CONTAINER_ID}" >/dev/null

echo ""
echo "=============================="
echo "TPC-H SF1 data generated successfully!"
echo "Dump file: ${DUMP_FILE}"
ls -lh "${DUMP_FILE}"
echo ""
echo "The evaluator will automatically use this dump on restore."
echo "=============================="
