# Frontend

React (Vite) app for the SASS MVP demo. It talks only to the backend (`Backend/`, port 8001),
never to the agentic service, so the service token stays out of the browser.

## Screens

| Screen | Route | Backend call |
|---|---|---|
| Classrooms | `/` | `GET /classrooms` |
| Students + **Review class** | `/classrooms/:id` | `GET /classrooms/{id}/students`, `POST /classrooms/{id}/reviews`, `GET /reviews/{run_id}` (every 2 s) |
| Findings | `/classrooms/:id/students/:studentId` | `GET /students/{id}/findings` |

## Run

Needs Node.js 18+ (`brew install node`).

```bash
cd Frontend
npm install
npm run dev
```

Open http://localhost:5173. The backend must be running on port 8001 (see `Backend/README.md`),
and for the Review class button the agentic service on port 8000 as well.

To point at a different backend, copy `.env.example` to `.env` and change `VITE_API_URL`.

## Demo

1. `bash Backend/scripts/fill_demo_data.sh` (20 students, nothing reviewed)
2. Start the agentic service, the backend and `npm run dev`
3. Classroom 1.01 → **Review class** → watch "Reviewing… n of 20"
4. Open a student to see the findings
5. Reset for the next run: `cd AgenticAI && python -m agentic reset-reviews --classroom 1.01`
