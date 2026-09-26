.PHONY: install backend frontend test backend-prepare backend-prepare-force

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# 体系文档启动数据准备：预检/状态查看/强制重建
backend-prepare:
	cd backend && .venv/bin/python -m app.prepare_data

backend-prepare-force:
	cd backend && .venv/bin/python -m app.prepare_data --force

test:
	cd backend && .venv/bin/python -m unittest discover -s tests -v
