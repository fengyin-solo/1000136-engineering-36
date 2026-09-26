.PHONY: install prepare backend frontend test

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

prepare:
	cd backend && .venv/bin/python -m app.bootstrap

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/python -m unittest discover -s tests -t . -v
