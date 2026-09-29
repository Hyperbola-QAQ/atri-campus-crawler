"""Fetch the HNUCM payment portal's valid dorm-room options into the catalog."""

import asyncio

from services.electricity import ElectricityService


async def main() -> None:
    catalog = await ElectricityService.from_environment().refresh_room_catalog()
    campuses = {room["campus"] for room in catalog["rooms"]}
    print(f"已同步 {len(campuses)} 个校区、{len(catalog['rooms'])} 个寝室")


if __name__ == "__main__":
    asyncio.run(main())
