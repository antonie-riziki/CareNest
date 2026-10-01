from django.contrib import admin

from apps.wallet.models import Escrow, PayoutMethod, Settlement, Transaction, Wallet

admin.site.register(Wallet)
admin.site.register(Transaction)
admin.site.register(Escrow)
admin.site.register(PayoutMethod)
admin.site.register(Settlement)
