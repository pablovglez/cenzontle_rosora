"""
Configures a logger with console and time-rotated file handlers.
"""
import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler


class LoggerManager:
    """Manages logging configuration for a service.

    Args:
            name (str): The name of the logger.
            level (str): The logging level (e.g., logging.INFO).
            service_type (str): The name of service used in log formatting and file naming.

    """
    def __init__(self, name, level, service_type):
        self.name = name
        self.level = level
        self.service_type = service_type
        self.logger = logging.getLogger(name)
        self.child_loggers = []

    def config_logger(self):
        """Configures and returns a logger with console and time-rotated file handlers.

        Return:
            logger (getLogger): Configured logger instance.
        """

        # Normalize level if provided as a string (e.g. 'INFO')
        if isinstance(self.level, str):
            numeric_level = getattr(logging, self.level.upper(), None)
            if not isinstance(numeric_level, int):
                raise ValueError(f"Invalid log level: {self.level}")
            self.level = numeric_level

        self.logger.setLevel(self.level)

        formatter = logging.Formatter("%(asctime)s [%(service_type)s / %(name)s] [%(levelname)s] %(message)s")

        # Add a console handler only if there's not already one attached
        if not any(isinstance(h, logging.StreamHandler) for h in self.logger.handlers):
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(self.level)
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)

        # Add a time rotated file handler only if there's not already one attached
        if not any(isinstance(h, TimedRotatingFileHandler) for h in self.logger.handlers):
            log_file = f"{self.service_type.lower()}.log"
            log_path = f"../log/{log_file}"
            try:
                main_handler = TimedRotatingFileHandler(filename=log_path,
                                                        when="midnight",
                                                        backupCount=5,
                                                        utc=True,
                                                        encoding="utf-8")
            except FileNotFoundError:
                # If the log directory doesn't exist, create it and try again
                os.makedirs("../log", exist_ok=True)
                main_handler = TimedRotatingFileHandler(filename=log_path,
                                                        when="midnight",
                                                        backupCount=5,
                                                        utc=True,
                                                        encoding="utf-8")
            main_handler.setLevel(self.level)
            # Include service_type in the formatter using the '%(service_type)s' field
            main_handler.setFormatter(formatter)
            self.logger.addHandler(main_handler)

        self.logger = logging.LoggerAdapter(self.logger, {"service_type": self.service_type})

    def add_child_logger(self, child_name):
        child_logger = self.logger.logger.getChild(child_name)
        child_logger = logging.LoggerAdapter(child_logger, self.logger.extra)
        self.child_loggers.append(child_logger)
        return child_logger

    def update_logger_level(self, level):
        if isinstance(level, str):
            numeric_level = getattr(logging, level.upper(), None)
            if not isinstance(numeric_level, int):
                raise ValueError(f"Invalid log level: {level}")
            level = numeric_level

        for logger_element in [self.logger] + self.child_loggers:
            logger_element.setLevel(level)
            for handler in logger_element.logger.handlers:
                handler.setLevel(level)
                handler.flush()
