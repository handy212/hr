from django.contrib import admin

from .models import (
    TerminalCommand,
    TerminalDevice,
    TerminalEmployee,
    TerminalPunchLog,
    TerminalSnapshot,
    TerminalSyncTask,
    TerminalSystemRecord,
)

admin.site.register(TerminalDevice)
admin.site.register(TerminalEmployee)
admin.site.register(TerminalSyncTask)
admin.site.register(TerminalCommand)
admin.site.register(TerminalPunchLog)
admin.site.register(TerminalSystemRecord)
admin.site.register(TerminalSnapshot)
