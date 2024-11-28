import logging
import os
import sys
import time
from http import HTTPStatus

import requests
from telebot import TeleBot
from dotenv import load_dotenv

import exceptions as e

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PRACTICUM_TOKEN = os.getenv("PRACTICUM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

RETRY_PERIOD = 600
REQUEST_TIMEOUT_IN_SECONDS = 10
HEADERS = {"Authorization": f"OAuth {PRACTICUM_TOKEN}"}
ENDPOINT = "https://practicum.yandex.ru/api/user_api/homework_statuses/"


HOMEWORK_VERDICTS = {
    "reviewing": "Работа взята на проверку ревьюером.",
    "rejected": "Работа проверена: у ревьюера есть замечания.",
    "approved": "Работа проверена: ревьюеру всё понравилось. Ура!",
}


def check_tokens() -> bool:
    """Проверяет доступность необходимых переменных окружения."""
    return all([TELEGRAM_TOKEN, PRACTICUM_TOKEN, TELEGRAM_CHAT_ID])


def send_message(bot: TeleBot, message: str) -> None:
    """Отправляет сообщение в чат, определяемый окружением."""
    try:
        logging.debug(msg="Запускаем отправку сообщения в Телеграм")
        bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
        )
    except Exception as error:
        raise e.MessageNotSent(f"Ошибка при отправке: {error}")
    logging.debug(msg="Успешно отправили сообщение в Телеграм")


def get_api_answer(timestamp: int = 0) -> dict:
    """Делает запрос к единственному эндпоинту API сервиса Домашка."""
    try:
        logging.info(msg="Посылаем запрос к эндпоинту API")
        request_params = {"url": ENDPOINT,
                          "headers": HEADERS,
                          "timeout": REQUEST_TIMEOUT_IN_SECONDS,
                          "params": {"from_date": timestamp}}
        response = requests.get(**request_params)

    except requests.RequestException as error:
        raise e.RequestError(f"Ошибка при совершении запроса к API: {error}")

    logging.info(msg="Получили ответ от эндпоинта API")
    status = HTTPStatus(value=response.status_code)
    if status != HTTPStatus.OK:
        raise e.RequestError(
            f"Статус ответа: {status.value} {status.phrase}. "
            f"Полный текст: {response.text}"
        )
    logging.info(msg="Статус ответа OK")

    try:
        logging.info(msg="Преобразуем ответ в словарь")
        result = response.json()
    except ValueError as error:
        raise e.UnexpectedResponseData(f"Не удалось распарсить ответ: {error}")

    logging.info(msg="Успешно привели ответ к словарю")
    return result


def check_response(response: dict) -> None:
    """Проверяет преобразованный ответ на соответствие документации."""
    if not isinstance(response, dict):
        raise TypeError(
            "Тип данных ответа отличается от dict"
        )
    if "homeworks" not in response:
        raise KeyError(
            "В ответе отсутствует ключ со списком работ"
        )
    if not isinstance(response["homeworks"], list):
        raise TypeError(
            "Тип данных списка работ отличается от list"
        )
    if not response["homeworks"]:
        raise e.UnexpectedResponseData(
            "API вернула пустой список домашних работ: "
            "ни одна работа пока не взята на проверку"
        )


def get_latest_homework(response: dict) -> dict:
    """
    Получает из ответа актуальную домашнюю работу.
    Для корректной работы функции необходимо предварительно гарантировать,
    что список работ в ответе не пуст.
    """
    for homework in response["homeworks"]:
        if "date_updated" not in homework:
            raise KeyError(
                "Не у всех работ указана дата обновления"
            )
    return sorted(response["homeworks"],
                  key=lambda homework: homework["date_updated"],
                  reverse=True)[0]


def parse_status(homework: dict) -> str:
    """
    Извлекает статус из домашней работы.
    Возвращает подготовленную для отправки в Telegram строку.
    """
    if "homework_name" not in homework:
        raise KeyError(
            "У домашней работы отсутствет название"
        )
    if "status" not in homework:
        raise KeyError(
            "У домашней работы отсутствет статус"
        )
    if homework["status"] not in HOMEWORK_VERDICTS:
        raise KeyError(
            "Домашняя работа содержит неизвестный статус"
        )

    homework_name = homework["homework_name"]
    verdict = HOMEWORK_VERDICTS[homework["status"]]
    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def main():
    """Основная логика работы бота."""
    logging.info(msg="Инициализируем бота")

    if not check_tokens():
        message = "Не все переменные окружения доступны"
        logging.critical(message)
        sys.exit(message)

    bot = TeleBot(token=TELEGRAM_TOKEN)
    logging.info(msg="Успешно завершили инициализацию бота")
    previous_status = ""

    while True:
        try:
            response = get_api_answer()
            check_response(response)
            homework = get_latest_homework(response)
            current_status = parse_status(homework)
        except (e.UnexpectedResponseData, e.RequestError,
                TypeError, KeyError) as error:
            current_status = f"Технические неполадки: {error}"
            logging.error(current_status)
        except Exception as error:
            current_status = f"Неизвестный сбой в работе: {error}"
            logging.error(current_status)

        if current_status == previous_status:
            logging.debug("Статус не изменился")
        else:
            try:
                send_message(bot=bot, message=current_status)
            except e.MessageNotSent as error:
                current_status = f"Не удалось отправить сообщение: {error}"
                logging.error(current_status)
            except Exception as error:
                current_status = f"Неизвестный сбой в работе: {error}"
                logging.error(current_status)

        previous_status = current_status
        time.sleep(RETRY_PERIOD)


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.DEBUG,
        filename="main.log",
        filemode="a",
        format="%(asctime)s, %(levelname)s, %(name)s, %(lineno)s, %(message)s",
    )

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(stream=sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s, %(levelname)s, %(name)s, %(lineno)s, %(message)s"
    )

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    main()
