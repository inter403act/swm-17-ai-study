package sample.task;

import java.time.LocalDateTime;

public record Task(
        Long id,
        Long ownerId,
        String title,
        TaskStatus status,
        LocalDateTime createdAt
) {
    public Task complete() {
        return new Task(
                id,
                ownerId,
                title,
                TaskStatus.DONE,
                createdAt
        );
    }
}