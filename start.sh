#!/usr/bin/env bash
set -e

# Paperclip AI — startup script

# Ensure PostgreSQL is running
if ! pg_lsclusters | grep -q online; then
  pg_ctlcluster 16 main start
fi

# Create DB/user if they don't exist
su -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='paperclip'\" | grep -q 1 || psql -c \"CREATE USER paperclip WITH PASSWORD 'paperclip' CREATEDB;\"" postgres
su -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='paperclip'\" | grep -q 1 || psql -c \"CREATE DATABASE paperclip OWNER paperclip;\"" postgres

# Install paperclipai if not present
if ! command -v paperclipai &>/dev/null; then
  npm install -g paperclipai
fi

# Run onboarding if no config exists, then start
if [ ! -f "$HOME/.paperclip/instances/default/config.json" ]; then
  paperclipai onboard -y
fi

paperclipai run -c "$HOME/.paperclip/instances/default/config.json"
