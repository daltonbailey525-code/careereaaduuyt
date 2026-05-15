# CareerEngine

CareerEngine is a full-stack basketball career tracker app.

It has:

- React frontend in `frontend/`
- FastAPI backend in `backend/`
- MongoDB storage for users, builds, games, attributes, badges and settings

## Local setup

### 1. Backend

Create a backend environment file:

```bash
cd backend
cp .env.example .env
```

Edit `backend/.env` and set:

```env
MONGO_URL=mongodb://localhost:27017
DB_NAME=careerengine
JWT_SECRET=replace-this-with-a-long-random-secret
CORS_ORIGINS=http://localhost:3000
```

Install and run:

```bash
pip install -r requirements.txt
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

The API should respond at:

```text
http://localhost:8000/api/
```

### 2. Frontend

Create a frontend environment file:

```bash
cd frontend
cp .env.example .env
```

Make sure it contains:

```env
REACT_APP_BACKEND_URL=http://localhost:8000
```

Install and run:

```bash
npm install --legacy-peer-deps
npm start
```

The site should open at:

```text
http://localhost:3000
```

## Deployment path

Recommended simple deployment:

1. Create a free MongoDB Atlas cluster.
2. Deploy the backend to Render as a Python Web Service.
3. Deploy the frontend to Vercel as a Create React App project.
4. Put the Render backend URL into Vercel as `REACT_APP_BACKEND_URL`.
5. Put the Vercel frontend URL into Render as `CORS_ORIGINS`.

## Render backend settings

Use these settings if deploying manually:

```text
Root Directory: backend
Build Command: pip install -r requirements.txt
Start Command: uvicorn server:app --host 0.0.0.0 --port $PORT
```

Required environment variables:

```env
MONGO_URL=<your MongoDB Atlas connection string>
DB_NAME=careerengine
JWT_SECRET=<long random secret>
CORS_ORIGINS=https://your-frontend.vercel.app
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=<optional admin password>
```

## Vercel frontend settings

Use these settings:

```text
Root Directory: frontend
Build Command: npm run build
Output Directory: build
```

Required environment variable:

```env
REACT_APP_BACKEND_URL=https://your-backend.onrender.com
```


## Overall calculation

The backend now uses one universal 2K-style overall formula for every position. It does not use PG, SG, SF, PF or C-specific weighting.

Attribute value groups:

- Highest value: Speed, Agility, Vertical, Mid-Range Shot and Three-Point Shot
- High value: Driving Layup, Driving Dunk, Standing Dunk, Steal and Block
- Medium value: Pass Accuracy, Ball Handle, Speed With Ball, Offensive Rebound and Defensive Rebound
- Low value: everything else

The weighted score is curved into a 2K-style overall with `20 + weighted_rating * 0.88`, capped at 99. The dashboard overall and build summary overall both come from this same formula.

## Notes

The frontend uses Create React App, so any environment variable that needs to be available in the browser must start with `REACT_APP_`.

The backend reads environment variables on startup, so missing `MONGO_URL`, `DB_NAME` or `JWT_SECRET` will prevent the backend from starting.

The backend requirements were cleaned to include only the packages this app imports. The original ZIP included an `emergentintegrations` package that is not used by the app and can break normal deployment installs.
