# Deploying NWS NBM Viewer on AWS ECS (Fargate)

Two services behind one ALB — no sidecar needed for the app (the edge gateway
from `docker/nginx.Dockerfile` is for single-host Docker Compose; on AWS the
ALB terminates TLS and the frontend/backend split by path instead):

```
 ALB
  ├── /*          → nbm-viewer-frontend   (Next.js, :3000)
  └── /api/v1/*   → nbm-viewer-backend    (FastAPI, :8000)   ← listener rule precedence
 /api/health    → frontend  (Next.js route hitting backend /health/live)
```

State shared across backend tasks:

| State                    | Where                                            |
| ------------------------ | ------------------------------------------------ |
| Tile + index cache       | EFS (`/data/tilecache`) — flock coordinates the poller across tasks |
| Run pointer / meta       | ElastiCache Redis (`CACHE_BACKEND=redis`)        |

## 1. Images

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
for app in backend frontend; do
  aws ecr describe-repositories --repository-names nbm-viewer/$app 2>/dev/null ||
    aws ecr create-repository --repository-name nbm-viewer/$app --image-scanning-configuration scanOnPush=true
done
docker login --username AWS --password "$(aws ecr get-login-password --region $REGION)" $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

cd ../..
docker build -t nbm-viewer/backend:prod  --target prod -f docker/backend.Dockerfile backend
docker build -t nbm-viewer/frontend:prod --target prod -f docker/frontend.Dockerfile frontend
docker tag nbm-viewer/backend:prod  $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/nbm-viewer/backend:prod
docker tag nbm-viewer/frontend:prod $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/nbm-viewer/frontend:prod
docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/nbm-viewer/backend:prod
docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/nbm-viewer/frontend:prod
```

## 2. EFS for the tile cache

```bash
aws efs create-file-system --performance-mode generalPurpose --encrypted
# Access point pins ownership so the non-root `nbm` (uid 1001) can write:
aws efs create-access-point --file-system-id fs-XXXX \
  --posix 'Uid=1001,Gid=1001,EnablePosixOwnership=true' \
  --root-directory 'Path=/tilecache,CreationInfo={Uid=1001,Gid=1001,Mode=0755}'
```

The diskcache + `poller.lock` design is multi-task safe: SQLite WAL over EFS
is fine for this write volume (the backend serialises writers via `flock`),
and the poller lock elects exactly one task to run the NOAA loop.

## 3. Redis (ElastiCache)

Any `cache.m6g.large`-class serverless cache in the same VPC; store the
endpoint in Secrets Manager as `nbm-viewer/redis-url` (`redis://host:6379/0`).
The backend degrades to the EFS disk cache automatically if Redis is
unreachable — so this is optional but recommended (shared run-pointer +
metadata across tasks with zero disk contention).

## 4. Tasks, service, ALB

```bash
# Render the task definitions in this folder (replace ACCOUNT_ID/REGION/fs-…), then:
aws ecs register-task-definition --cli-input-json file://nbm-viewer-backend-task-definition.json
aws ecs register-task-definition --cli-input-json file://nbm-viewer-frontend-task-definition.json

aws ecs create-cluster --cluster-name nbm-viewer
aws ecs create-service --cluster nbm-viewer --service-name backend \
  --task-definition nbm-viewer-backend --launch-type FARGATE --desired-count 2 \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-priv…],securityGroups=[sg-…],assignPublicIp=DISABLED}"
aws ecs create-service --cluster nbm-viewer --service-name frontend \
  --task-definition nbm-viewer-frontend --launch-type FARGATE --desired-count 1 \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-priv…],securityGroups=[sg-…],assignPublicIp=DISABLED}"
```

ALB: two target groups (`backend-tg` on 8000 via the ECS service,
`frontend-tg` on 3000). Listener rules in order:

1. path `/api/v1/*` → backend-tg
2. default → frontend-tg

Health checks: backend `/health/live`, frontend `/api/health`.

## 5. Optional: split the poller out

Scale the backend service to N stateless workers and disable in-task
scheduling (`SCHEDULER_ENABLED=false`), then run a one-task
`nbm-viewer-poller` service on the same EFS with
`docker/backend.Dockerfile` overridden to:

```
command: ["python", "-m", "app.cron.poller"]
environment: SCHEDULER_ENABLED=true;POLLER_ENABLED=true
```

This keeps NOAA polling + warm-up renders entirely off the request path.

## 6. Autoscaling & capacity notes

- Backend scales on ALB `RequestCountPerTarget` and `TargetResponseTime`
  (tile serving is CPU-light; rendering bursts dominate). Scale target ~400 ms.
- Each tile first miss = ≤ 7 ranged S3 GETs against NOAA's public bucket;
  the edge (ALB→backend) design caches each tile URL at the EFS diskcache
  layer for 72 h, so steady-state NOAA traffic is one LISTING/10 min per
  domain/product + index revalidations.
