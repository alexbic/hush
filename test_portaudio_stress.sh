#!/bin/bash
# test_portaudio_stress.sh - стресс-тест для воспроизведения PortAudio зависаний
# Цель: многократное запуска/остановка записи для воспроизранения зависаний

set -e

APP_PATH="$PWD/HUSH.app"
LOG_DIR="/tmp/hush_test_logs"
TEST_DURATION=300  # 5 минут тестирования
RECORD_CYCLES=50   # количество циклов записи/остановки

echo "=== Стресс-тест PortAudio для HUSH ==="
echo "Путь к приложению: $APP_PATH"
echo "Директория логов: $LOG_DIR"
echo "Длительность теста: $TEST_DURATION секунд"
echo "Количество циклов: $RECORD_CYCLES"
echo ""

# Создаем директорию для логов
mkdir -p "$LOG_DIR"

# Функция для логирования
log_test() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $1" | tee -a "$LOG_DIR/test.log"
}

# Функция для проверки состояния приложения
check_app_status() {
    if pgrep -f "HUSH.app" > /dev/null; then
        log_test "HUSH запущен (PID: $(pgrep -f HUSH.app))"
        return 0
    else
        log_test "HUSH не запущен"
        return 1
    fi
}

# Функция для запуска приложения
start_app() {
    log_test "Запуск HUSH..."
    open "$APP_PATH"
    sleep 3  # даем время на запуск
    
    if check_app_status; then
        log_test "HUSH успешно запущен"
        return 0
    else
        log_test "Ошибка: HUSH не запустился"
        return 1
    fi
}

# Функция для остановки приложения
stop_app() {
    log_test "Остановка HUSH..."
    pkill -f "HUSH.app" || true
    sleep 2
    
    if check_app_status; then
        log_test "Предупреждение: HUSH все еще запущен, принудительное завершение"
        pkill -9 -f "HUSH.app" || true
        sleep 1
    fi
    
    log_test "HUSH остановлен"
}

# Функция для симуляции записи
simulate_recording() {
    local duration=2  # 2 секунды "записи"
    
    log_test "Симуляция записи ($duration сек)..."
    
    # Имитируем нажатие Right Option (запись)
    # Используем osascript для симуляции клавиатуры
    osascript -e 'tell application "System Events" to key down {option right}' || true
    sleep $duration
    osascript -e 'tell application "System Events" to key up {option right}' || true
    
    log_test "Симуляция записи завершена"
}

# Основной тест
main() {
    log_test "Начало стресс-теста"
    log_test "Тест начат: $(date)"
    
    # Запускаем приложение
    if ! start_app; then
        log_test "Критическая ошибка: не удалось запустить приложение"
        exit 1
    fi
    
    # Основной цикл тестирования
    cycle=1
    start_time=$(date +%s)
    
    while [ $cycle -le $RECORD_CYCLES ]; do
        current_time=$(date +%s)
        elapsed_time=$((current_time - start_time))
        
        if [ $elapsed_time -ge $TEST_DURATION ]; then
            log_test "Достигнут лимит времени теста ($TEST_DURATION сек)"
            break
        fi
        
        log_test "Цикл $cycle/$RECORD_CYCLES (Прошло: $elapsed_time/$TEST_DURATION сек)"
        
        # Симулируем запись
        simulate_recording
        
        # Короткая пауза между циклами
        sleep 1
        
        cycle=$((cycle + 1))
    done
    
    # Останавливаем приложение
    stop_app
    
    log_test "Стресс-тест завершен"
    log_test "Тест завершен: $(date)"
    
    # Анализируем логи
    log_test "Анализ логов..."
    if [ -f "$LOG_DIR/test.log" ]; then
        echo ""
        echo "=== Анализ логов ==="
        
        # Проверяем на наличие ошибок
        error_count=$(grep -i "error\|failed\|exception" "$LOG_DIR/test.log" | wc -l)
        hang_count=$(grep -i "hang\|freeze\|stuck" "$LOG_DIR/test.log" | wc -l)
        
        echo "Количество ошибок: $error_count"
        echo "Количество упоминаний зависаний: $hang_count"
        
        # Показываем последние 10 строк лога
        echo ""
        echo "Последние 10 строк лога:"
        tail -10 "$LOG_DIR/test.log"
    fi
    
    echo ""
    echo "=== Тест завершен ==="
    echo "Логи сохранены в: $LOG_DIR/test.log"
    echo "Для анализа результатов проверьте директорию: $LOG_DIR"
}

# Запускаем основной тест
main