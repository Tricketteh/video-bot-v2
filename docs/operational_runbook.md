# Operational Runbook

## Перезапуск

```bash
docker compose restart bot
docker compose restart photo-service
```

Полный пересбор:

```bash
docker compose down
docker compose up -d --build
```

## Логи

```bash
docker compose logs -f bot
docker compose logs -f photo-service
```

Ищите поля: `platform`, `reason`, `message`.

## Обновление cookies

1. Обновить файл в `cookies/<platform>.txt` (формат Netscape cookies.txt).
2. Проверить права доступа на чтение.
3. Перезапустить сервис:

```bash
docker compose restart bot photo-service
```

## Типовые сообщения пользователям

- `Login required`: требуется актуальный cookies-файл для платформы.
- `Rate-limited`: временное ограничение платформы, попросить повторить позже.
- `cookiefile missing`: отсутствует `cookies/<platform>.txt`.

## Healthcheck

- Bot: `http://localhost:8090/health`
- Photo service: `http://localhost:8080/health`

Оба endpoint возвращают `status=ok` и timestamp последней успешной загрузки (если есть).