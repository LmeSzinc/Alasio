from alasio.ext import env

if not env.PROJECT_ROOT:
    env.set_project_root(__file__, 2)
