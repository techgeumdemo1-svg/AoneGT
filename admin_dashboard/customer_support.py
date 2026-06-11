from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.models import SupportChatMessage, SupportTicket

from .activity_log_utils import record_admin_activity
from .customers import _customer_display_name, _customers_queryset
from .models import AdminActivityLog
from .orders import _paginate_queryset
from .views import IsStaffUser

User = get_user_model()


def _parse_positive_int_query_param(request, name, *, required=True):
    raw = (request.query_params.get(name) or '').strip()
    if not raw:
        if required:
            return None, Response(
                {
                    'detail': (
                        f'Query parameter {name} is required and must be a positive integer.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None, None
    if not raw.isdigit():
        return None, Response(
            {
                'detail': (
                    f'Query parameter {name} is required and must be a positive integer.'
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(raw), None


def _support_tickets_queryset():
    return (
        SupportTicket.objects.select_related('user', 'order', 'assigned_to')
        .annotate(messages_count=Count('messages', distinct=True))
    )


def _sender_display_name(user) -> str:
    if not user:
        return 'System'
    return _customer_display_name(user) if not (user.is_staff or user.is_superuser) else (
        _customer_display_name(user) or user.email
    )


class AdminSupportChatMessageSerializer(serializers.ModelSerializer):
    message_id = serializers.IntegerField(source='id', read_only=True)
    sender_id = serializers.IntegerField(source='sender_id', read_only=True, allow_null=True)
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = SupportChatMessage
        fields = (
            'message_id',
            'sender_id',
            'sender_name',
            'sender_role',
            'message',
            'is_read_by_customer',
            'is_read_by_staff',
            'created_at',
        )
        read_only_fields = fields

    def get_sender_name(self, obj):
        return _sender_display_name(obj.sender)


class AdminSupportTicketListSerializer(serializers.ModelSerializer):
    ticket_id = serializers.IntegerField(source='id', read_only=True)
    customer_id = serializers.IntegerField(source='user_id', read_only=True)
    customer_name = serializers.SerializerMethodField()
    customer_email = serializers.EmailField(source='user.email', read_only=True)
    order_id = serializers.IntegerField(read_only=True, allow_null=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    priority_label = serializers.CharField(source='get_priority_display', read_only=True)
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    assigned_to_id = serializers.IntegerField(read_only=True, allow_null=True)
    assigned_to_name = serializers.SerializerMethodField()
    messages_count = serializers.IntegerField(read_only=True)
    last_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = (
            'ticket_id',
            'ticket_number',
            'customer_id',
            'customer_name',
            'customer_email',
            'order_id',
            'subject',
            'category',
            'category_label',
            'status',
            'status_label',
            'priority',
            'priority_label',
            'assigned_to_id',
            'assigned_to_name',
            'messages_count',
            'last_message_preview',
            'last_message_at',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    def get_customer_name(self, obj):
        return _customer_display_name(obj.user)

    def get_assigned_to_name(self, obj):
        if not obj.assigned_to_id:
            return ''
        return _sender_display_name(obj.assigned_to)

    def get_last_message_preview(self, obj):
        last = obj.messages.order_by('-created_at').first()
        if not last:
            return ''
        text = (last.message or '').strip()
        return text[:120] + ('…' if len(text) > 120 else '')


class AdminSupportTicketUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=SupportTicket.Status.choices,
        required=False,
    )
    priority = serializers.ChoiceField(
        choices=SupportTicket.Priority.choices,
        required=False,
    )
    assigned_to_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_assigned_to_id(self, value):
        if value is None:
            return value
        staff = User.objects.filter(pk=value, is_staff=True).first()
        if not staff:
            raise serializers.ValidationError('Assigned user must be an active staff member.')
        return value


class AdminSupportTicketReplySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=5000)

    def validate_message(self, value):
        text = (value or '').strip()
        if not text:
            raise serializers.ValidationError('Message cannot be empty.')
        return text


def _apply_support_ticket_filters(queryset, request):
    customer_id = (request.query_params.get('customer_id') or '').strip()
    if customer_id.isdigit():
        queryset = queryset.filter(user_id=int(customer_id))

    status_filter = (request.query_params.get('status') or '').strip().lower()
    if status_filter in SupportTicket.Status.values:
        queryset = queryset.filter(status=status_filter)

    priority = (request.query_params.get('priority') or '').strip().lower()
    if priority in SupportTicket.Priority.values:
        queryset = queryset.filter(priority=priority)

    category = (request.query_params.get('category') or '').strip().lower()
    if category in SupportTicket.Category.values:
        queryset = queryset.filter(category=category)

    assigned_to_id = (request.query_params.get('assigned_to_id') or '').strip()
    if assigned_to_id.isdigit():
        queryset = queryset.filter(assigned_to_id=int(assigned_to_id))

    search = (request.query_params.get('search') or '').strip()
    if search:
        q = (
            Q(subject__icontains=search)
            | Q(ticket_number__icontains=search)
            | Q(user__email__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
        )
        if search.isdigit():
            q |= Q(pk=int(search)) | Q(user_id=int(search)) | Q(order_id=int(search))
        queryset = queryset.filter(q)

    return queryset


def _ticket_detail_payload(ticket: SupportTicket) -> dict:
    messages = ticket.messages.select_related('sender').order_by('created_at')
    data = AdminSupportTicketListSerializer(ticket).data
    data['chat_history'] = AdminSupportChatMessageSerializer(messages, many=True).data
    data['chat_messages_count'] = messages.count()
    return data


class AdminCustomerSupportTicketAPIView(APIView):
    """
    GET   /api/admin/customers/support-tickets/          — list (filter: customer_id, status, …)
    GET   /api/admin/customers/support-tickets/?id=<id> — ticket detail + chat history
    PATCH /api/admin/customers/support-tickets/?id=<id> — update status / priority / assignee
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        ticket_id_raw = (request.query_params.get('id') or '').strip()
        if ticket_id_raw:
            ticket_id, err = _parse_positive_int_query_param(request, 'id')
            if err:
                return err
            ticket = get_object_or_404(
                _support_tickets_queryset().prefetch_related('messages__sender'),
                pk=ticket_id,
            )
            SupportChatMessage.objects.filter(
                ticket=ticket,
                sender_role=SupportChatMessage.SenderRole.CUSTOMER,
                is_read_by_staff=False,
            ).update(is_read_by_staff=True)
            return Response(_ticket_detail_payload(ticket), status=status.HTTP_200_OK)

        qs = _apply_support_ticket_filters(
            _support_tickets_queryset(),
            request,
        ).order_by('-last_message_at', '-created_at')
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                **pagination,
                'results': AdminSupportTicketListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request):
        ticket_id, err = _parse_positive_int_query_param(request, 'id')
        if err:
            return err
        ticket = get_object_or_404(_support_tickets_queryset(), pk=ticket_id)
        serializer = AdminSupportTicketUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data:
            return Response({'detail': 'No fields to update.'}, status=status.HTTP_400_BAD_REQUEST)

        update_fields = ['updated_at']
        if 'status' in data:
            ticket.status = data['status']
            update_fields.append('status')
        if 'priority' in data:
            ticket.priority = data['priority']
            update_fields.append('priority')
        if 'assigned_to_id' in data:
            ticket.assigned_to_id = data['assigned_to_id']
            update_fields.append('assigned_to')
        ticket.save(update_fields=update_fields)

        record_admin_activity(
            request,
            category=AdminActivityLog.Category.CUSTOMERS,
            action='support_ticket.updated',
            message=f'Updated support ticket {ticket.ticket_number}.',
            target_type='support_ticket',
            target_id=ticket.pk,
            metadata={'fields': sorted(data.keys())},
        )
        return Response(
            {
                'message': 'Support ticket updated.',
                'ticket': _ticket_detail_payload(ticket),
            },
            status=status.HTTP_200_OK,
        )


class AdminCustomerSupportChatAPIView(APIView):
    """
    GET  /api/admin/customers/support-tickets/chat/?ticket_id=<id> — chat history
    POST /api/admin/customers/support-tickets/chat/?ticket_id=<id> — staff reply
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        ticket_id, err = _parse_positive_int_query_param(request, 'ticket_id')
        if err:
            return err
        ticket = get_object_or_404(SupportTicket.objects.select_related('user'), pk=ticket_id)
        qs = ticket.messages.select_related('sender').order_by('created_at')
        page_qs, pagination = _paginate_queryset(qs, request)
        SupportChatMessage.objects.filter(
            ticket=ticket,
            sender_role=SupportChatMessage.SenderRole.CUSTOMER,
            is_read_by_staff=False,
        ).update(is_read_by_staff=True)
        return Response(
            {
                'ticket_id': ticket.pk,
                'ticket_number': ticket.ticket_number,
                'customer_id': ticket.user_id,
                **pagination,
                'results': AdminSupportChatMessageSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        ticket_id, err = _parse_positive_int_query_param(request, 'ticket_id')
        if err:
            return err
        ticket = get_object_or_404(SupportTicket.objects.select_related('user'), pk=ticket_id)
        serializer = AdminSupportTicketReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message_text = serializer.validated_data['message']

        chat_message = SupportChatMessage.objects.create(
            ticket=ticket,
            sender=request.user,
            sender_role=SupportChatMessage.SenderRole.STAFF,
            message=message_text,
            is_read_by_staff=True,
            is_read_by_customer=False,
        )
        now = timezone.now()
        ticket.last_message_at = now
        update_fields = ['last_message_at', 'updated_at']
        if ticket.status == SupportTicket.Status.OPEN:
            ticket.status = SupportTicket.Status.IN_PROGRESS
            update_fields.append('status')
        if not ticket.assigned_to_id:
            ticket.assigned_to = request.user
            update_fields.append('assigned_to')
        ticket.save(update_fields=update_fields)

        record_admin_activity(
            request,
            category=AdminActivityLog.Category.CUSTOMERS,
            action='support_ticket.replied',
            message=f'Replied on support ticket {ticket.ticket_number}.',
            target_type='support_ticket',
            target_id=ticket.pk,
        )
        return Response(
            {
                'message': 'Reply sent.',
                'ticket_id': ticket.pk,
                'ticket_number': ticket.ticket_number,
                'chat_message': AdminSupportChatMessageSerializer(chat_message).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AdminCustomerSupportTicketsByCustomerAPIView(APIView):
    """GET /api/admin/customers/support-tickets/by-customer/?customer_id=<id>"""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        customer_id, err = _parse_positive_int_query_param(request, 'customer_id')
        if err:
            return err
        customer = get_object_or_404(_customers_queryset(), pk=customer_id)
        qs = _apply_support_ticket_filters(
            _support_tickets_queryset().filter(user=customer),
            request,
        ).order_by('-last_message_at', '-created_at')
        page_qs, pagination = _paginate_queryset(qs, request)
        return Response(
            {
                'customer_id': customer.pk,
                'customer_name': _customer_display_name(customer),
                'customer_email': customer.email,
                **pagination,
                'results': AdminSupportTicketListSerializer(page_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )
