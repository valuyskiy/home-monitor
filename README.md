# Home Monitor

Небольшой сервис для VPS, который проверяет доступность домашнего интернет-шлюза и Home Assistant и отправляет уведомления в Telegram.

Проект рассчитан на самостоятельную установку и адаптацию под собственную инфраструктуру. Он использует только стандартную библиотеку Python 3 и системную утилиту `ping`; внешние Python-пакеты не нужны.

## Возможности

- Проверка доступности публичного IPv4-адреса домашнего маршрутизатора по ICMP.
- Проверка Home Assistant через авторизованный API-запрос к `/api/config`.
- Уведомления в Telegram при подтверждённом отказе и восстановлении.
- Защита от кратковременных сбоев: отдельные пороги последовательных ошибок и успешных проверок.
- Ежедневный контрольный отчёт в Telegram.
- Сохранение состояния между перезапусками.
- Запуск как `systemd`-служба, автоматический перезапуск после сбоя.
- Логирование в файл и journald, ротация логов через logrotate.

## Схема работы

```text
                 VPS (Linux)
              ┌─────────────────────┐
              │ home-monitor        │
              │                     │
              │ 1. ICMP ping        │──────► Публичный IP роутера
              │ 2. HTTP API request │──────► Home Assistant /api/config
              │ 3. Telegram API     │──────► Telegram Bot API
              └─────────────────────┘
```

Мониторинг выполняется с VPS, то есть вне домашней сети. Поэтому сервис способен сообщить о потере доступа к домашнему шлюзу, даже если домашний интернет полностью недоступен, при условии что сам VPS и Telegram доступны.

### Что именно проверяется

1. **Контрольный IP** — отвечает ли указанный IPv4-адрес на ICMP Echo Request. Обычно это публичный адрес домашнего маршрутизатора.
2. **Home Assistant** — доступен ли URL API, проходит ли авторизация и возвращается ли JSON с непустым полем `version`.

Проверка HA подтверждает доступность Core API, но не гарантирует исправность всех интеграций, устройств, автоматизаций или Zigbee-сети.

## Требования

- VPS или другой постоянно работающий Linux-сервер с доступом в интернет.
- Ubuntu 22.04/24.04 или совместимый Linux.
- Python 3.10 или новее (требуется `zoneinfo`).
- Утилита `ping` (обычно пакет `iputils-ping`).
- Доступ VPS к контрольному IP по ICMP.
- Доступ VPS к Home Assistant по HTTP/HTTPS.
- Доступ VPS к `api.telegram.org` по HTTPS.
- Telegram-бот и chat ID.
- Long-Lived Access Token Home Assistant.

**Важно:** если провайдер или маршрутизатор блокирует ICMP, проверка IP будет показывать недоступность даже при работающем интернете. Убедитесь, что выбранный адрес отвечает на ping с VPS.

## Подготовка Telegram

1. В Telegram откройте `@BotFather`.
2. Создайте бота командой `/newbot`.
3. Сохраните выданный токен. Не публикуйте его и не отправляйте посторонним.
4. Откройте созданного бота и отправьте ему `/start`.
5. Узнайте свой numeric chat ID надёжным способом, например через собственного Telegram-бота для получения ID.
6. Укажите токен и chat ID в локальном файле `.env`.

Бот не сможет первым написать пользователю, пока пользователь не открыл с ним диалог.

## Подготовка Home Assistant

1. Войдите в Home Assistant под учётной записью, которой разрешено создавать токены.
2. Откройте профиль пользователя.
3. В разделе безопасности создайте Long-Lived Access Token.
4. Скопируйте токен сразу после создания и сохраните его в менеджере паролей.
5. Убедитесь, что `https://ваш-домен/api/config` доступен извне VPS и сертификат HTTPS действителен.

Не отключайте TLS-проверку ради обхода проблем с сертификатом. Исправьте DNS, сертификат или сетевую конфигурацию.

## Установка на Ubuntu

Ниже приведён пример установки в `/opt/home-monitor`.

### 1. Установить системные зависимости

```bash
sudo apt update
sudo apt install -y python3 iputils-ping logrotate
```

Проверьте:

```bash
python3 --version
ping -V
```

### 2. Получить проект

```bash
sudo git clone https://github.com/valuyskiy/home-monitor.git /opt/home-monitor
```

### 3. Создать конфигурацию

```bash
cd /opt/home-monitor
sudo cp .env.example .env
sudo nano .env
```

Заполните все значения своими данными. Не оставляйте демонстрационные значения из `.env.example`.

Ограничьте доступ к конфигурации:

```bash
sudo chown root:root .env
sudo chmod 600 .env
```

Файл `.env` содержит секреты. Он исключён из Git через `.gitignore`. Не публикуйте его, не прикладывайте к issue и не отправляйте в чаты.

### 4. Проверить доступность контрольного IP

```bash
ping -c 3 ВАШ_ПУБЛИЧНЫЙ_IP
```

Проверка должна проходить с VPS. Если ping не проходит, выясните, не блокируется ли ICMP на маршрутизаторе или у провайдера.

### 5. Проверить API Home Assistant

Не вставляйте токен непосредственно в командную строку, если сервером могут пользоваться другие пользователи. Для первичной проверки можно запустить запрос из защищённой административной сессии. Сам сервис использует токен из `.env`.

### 6. Создать системного пользователя и права

```bash
sudo useradd --system --home-dir /opt/home-monitor \
  --no-create-home --shell /usr/sbin/nologin homemonitor

sudo chown -R homemonitor:homemonitor /opt/home-monitor
sudo chmod 700 /opt/home-monitor
sudo chmod 600 /opt/home-monitor/.env
```

Скрипту нужен доступ к `.env`, `state.json` и `monitor.log`. Каталог принадлежит пользователю службы, чтобы сервис мог сохранять состояние и писать журнал.

На некоторых системах `ping` требует дополнительных прав для непривилегированного пользователя. Проверьте:

```bash
sudo -u homemonitor ping -n -c 1 -W 3 ВАШ_ПУБЛИЧНЫЙ_IP
```

Если появляется ошибка о недостатке прав, сначала проверьте настройки `ping` и sysctl. На Ubuntu обычно достаточно штатных разрешений `ping_group_range`. Не выдавайте всему Python-интерпретатору широкие привилегии без необходимости.

### 7. Установить systemd unit

В репозитории есть шаблон `systemd/home-monitor.service`. Для стандартного пути `/opt/home-monitor`:

```bash
sudo cp systemd/home-monitor.service /etc/systemd/system/home-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now home-monitor
```

Если изменили каталог установки или имя пользователя, скорректируйте `WorkingDirectory`, `ExecStart`, `User`, `Group` и `ReadWritePaths` в unit-файле.

### 8. Установить logrotate

```bash
sudo cp logrotate/home-monitor /etc/logrotate.d/home-monitor
sudo logrotate -d /etc/logrotate.d/home-monitor
```

Если каталог или пользователь отличаются от примера, обновите путь и директиву `su`.

### 9. Проверить работу

```bash
sudo systemctl status home-monitor --no-pager
sudo journalctl -u home-monitor -n 50 --no-pager
sudo tail -n 50 /opt/home-monitor/monitor.log
```

При первом запуске сервис отправляет стартовое сообщение с результатами проверок.

## Настройка `.env`

| Параметр | Назначение | Пример |
|---|---|---|
| `HOME_IP` | IPv4-адрес для ICMP-проверки | `203.0.113.10` |
| `HA_URL` | Полный URL API Home Assistant | `https://ha.example.com/api/config` |
| `TELEGRAM_TOKEN` | Токен Telegram-бота | секрет |
| `TELEGRAM_CHAT_ID` | ID чата-получателя | `123456789` |
| `HA_TOKEN` | Long-Lived Access Token HA | секрет |
| `CHECK_INTERVAL` | Интервал между циклами, секунд | `15` |
| `PING_TIMEOUT` | Тайм-аут ping, секунд | `3` |
| `HA_TIMEOUT` | Тайм-аут HA API, секунд | `10` |
| `TELEGRAM_TIMEOUT` | Тайм-аут Telegram API, секунд | `10` |
| `FAIL_THRESHOLD` | Число ошибок для уведомления о сбое | `3` |
| `RECOVERY_THRESHOLD` | Число успехов для уведомления о восстановлении | `2` |
| `DAILY_REPORT_ENABLED` | Включить ежедневный отчёт | `true` |
| `DAILY_REPORT_TIME` | Время ежедневного отчёта, `HH:MM` | `10:00` |
| `TIMEZONE` | Часовой пояс IANA | `Europe/Moscow` |

После изменения `.env` перезапустите службу:

```bash
sudo systemctl restart home-monitor
```

Изменения не применяются к уже работающему процессу автоматически.

### Как работает подтверждение событий

При `FAIL_THRESHOLD=3` уведомление о недоступности отправляется после трёх последовательных неудачных проверок. При `RECOVERY_THRESHOLD=2` восстановление подтверждается двумя последовательными успешными проверками.

При интервале 15 секунд это снижает вероятность ложных сообщений из-за кратковременного сбоя. Фактическое время обнаружения зависит от тайм-аутов и длительности запросов.

### Ежедневный отчёт

В заданное время сервис отправляет сообщение с результатами последней проверки. Дата успешной отправки сохраняется в `state.json`, поэтому после перезапуска отчёт за тот же день не дублируется.

Если VPS был выключен в момент отправки, отчёт будет отправлен при первом цикле после запуска, если заданное время в этот день уже наступило. При ошибке Telegram отправка повторяется в следующем цикле.

## Управление службой

```bash
sudo systemctl start home-monitor
sudo systemctl stop home-monitor
sudo systemctl restart home-monitor
sudo systemctl status home-monitor --no-pager
```

Просмотр журнала в реальном времени:

```bash
sudo journalctl -u home-monitor -f
```

Последние сообщения:

```bash
sudo journalctl -u home-monitor -n 100 --no-pager
```

## Резервное копирование

Для восстановления нужны:

- файлы проекта (`monitor.py`, systemd unit и logrotate config);
- `.env` — конфигурация и секреты;
- `state.json` — сохранённое состояние и дата последнего ежедневного отчёта.

Журнал `monitor.log` для восстановления работы не обязателен.

Пример создания архива конфигурации и состояния:

```bash
sudo tar -czf /root/home-monitor-backup-$(date +%F).tar.gz \
  -C /opt home-monitor/.env home-monitor/state.json
```

Такой архив содержит секреты. Ограничьте доступ:

```bash
sudo chmod 600 /root/home-monitor-backup-*.tar.gz
```

Храните резервную копию вне VPS в защищённом месте. Не загружайте архив в публичный GitHub.

Для полноценной резервной копии сохраните также используемую версию проекта или Git commit hash и копии файлов конфигурации systemd/logrotate.

## Восстановление на новом сервере

1. Установите Python, `iputils-ping`, `logrotate` и Git.
2. Скопируйте репозиторий в `/opt/home-monitor` или клонируйте его.
3. Создайте системного пользователя `homemonitor`.
4. Восстановите `.env` из защищённой резервной копии.
5. При необходимости восстановите `state.json`.
6. Установите права и владельца:

```bash
sudo chown -R homemonitor:homemonitor /opt/home-monitor
sudo chmod 700 /opt/home-monitor
sudo chmod 600 /opt/home-monitor/.env
```

7. Установите unit и logrotate config:

```bash
sudo cp /opt/home-monitor/systemd/home-monitor.service \
  /etc/systemd/system/home-monitor.service
sudo cp /opt/home-monitor/logrotate/home-monitor \
  /etc/logrotate.d/home-monitor
sudo systemctl daemon-reload
sudo systemctl enable --now home-monitor
```

8. Проверьте журнал и тестовое сообщение в Telegram.

Если `state.json` не восстанавливать, сервис начнёт с неизвестного состояния и отправит стартовое сообщение. Если восстановить файл, сервис сохранит известное состояние и дату последнего отчёта.

## Обновление проекта

Перед обновлением сделайте резервную копию `.env` и `state.json`.

Для установки обновления из Git:

```bash
cd /opt/home-monitor
sudo systemctl stop home-monitor
sudo git pull --ff-only
sudo python3 -m py_compile monitor.py
sudo systemctl start home-monitor
sudo systemctl status home-monitor --no-pager
```

Проверьте журнал после запуска. Не заменяйте `.env` файлом `.env.example`.

## Диагностика

### Служба не запускается

```bash
sudo systemctl status home-monitor --no-pager
sudo journalctl -u home-monitor -n 100 --no-pager
```

Проверьте наличие `.env`, обязательные параметры, права на каталог и синтаксис:

```bash
sudo -u homemonitor test -r /opt/home-monitor/.env && echo "env readable"
sudo python3 -m py_compile /opt/home-monitor/monitor.py
```

### Контрольный IP постоянно недоступен

```bash
ping -n -c 3 ВАШ_ПУБЛИЧНЫЙ_IP
sudo -u homemonitor ping -n -c 3 ВАШ_ПУБЛИЧНЫЙ_IP
```

Проверьте ICMP-фильтрацию и правильность IP. ICMP может блокироваться независимо от доступности TCP/интернета.

### Home Assistant недоступен

Проверьте:
- правильность `HA_URL`;
- доступность домена и порта с VPS;
- действительность HA token;
- корректность TLS-сертификата;
- правила firewall, reverse proxy и ограничения доступа.

### Telegram не отправляет сообщения

Проверьте токен бота, chat ID, наличие диалога с ботом и исходящий HTTPS-доступ к `api.telegram.org`. Не публикуйте токен в логах или обращениях за поддержкой.

### Отчёт не пришёл

Проверьте `DAILY_REPORT_ENABLED`, `DAILY_REPORT_TIME`, `TIMEZONE`, системное время и поле `daily_report_date` в `state.json`. Если дата уже равна текущей локальной дате, повторного отчёта не будет.

## Безопасность

- Используйте HTTPS с действительным сертификатом для Home Assistant.
- Выделенный системный пользователь запускает сервис без root-привилегий.
- Не открывайте Home Assistant в интернет только ради этого проекта без оценки рисков. Предпочтительны защищённый reverse proxy, VPN или иной контролируемый способ доступа.

## Ограничения

- Это простой монитор доступности, а не полноценная система мониторинга и оповещения с гарантированной доставкой.
- Если VPS, его сеть или Telegram недоступны, уведомление может задержаться или не дойти.
- ICMP-проверка зависит от разрешения ping на целевом узле.
- API-проверка HA не проверяет работоспособность всех компонентов Home Assistant.
- Состояние хранится локально в JSON-файле; для высоконагруженных и критичных систем следует использовать специализированную систему мониторинга.

## Лицензия

Проект распространяется по лицензии MIT. См. файл [LICENSE](LICENSE).
