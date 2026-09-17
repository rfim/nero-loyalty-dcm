select
    pointer_name,
    current_batch_id,
    current_release_at,
    updated_at
from {{ source('nero_control', 'release_pointer') }}
