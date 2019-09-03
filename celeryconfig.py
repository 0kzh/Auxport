from kombu import Exchange, Queue

broker_url = 'pyamqp://'
result_backend = 'redis://:XXXXXXXXXX@localhost:6379/0'
result_persistent = True
task_queues = (
    Queue('tasks', Exchange('tasks'), routing_key='tasks',
          queue_arguments={'x-max-priority': 10}),
)
worker_prefetch_multiplier = 1
task_acks_late = True