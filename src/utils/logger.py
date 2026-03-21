from colorama import Fore, Style, init

init(autoreset=True)


class _Logger:
    def info(self, msg: str) -> None:
        print(f"{Fore.GREEN}[INFO]{Style.RESET_ALL} {msg}")

    def warn(self, msg: str) -> None:
        print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} {msg}")

    def error(self, msg: str) -> None:
        print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} {msg}")

    def debug(self, msg: str) -> None:
        print(f"{Fore.CYAN}[DEBUG]{Style.RESET_ALL} {msg}")


logger = _Logger()
