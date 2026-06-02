package sample.task;

import java.util.Comparator;
import java.util.List;

public class TaskService {
    private static final int DEFAULT_LIMIT = 20;
    private final List<Task> tasks;

    public TaskService(List<Task> tasks) {
        this.tasks = tasks;
    }

    public List<Task> findTasks(Long ownerId, int limit) {
        int safeLimit = limit <= 0 ? DEFAULT_LIMIT : limit;

        return tasks.stream()
                .filter(task -> task.ownerId().equals(ownerId))
                .sorted(Comparator.comparing(Task::createdAt).reversed())
                .limit(safeLimit)
                .toList();
    }

    public Task complete(Long taskId, Long ownerId) {
        Task task = tasks.stream()
                .filter(item -> item.id().equals(taskId))
                .findFirst()
                .orElseThrow();

        if (!task.ownerId().equals(ownerId)) {
            throw new IllegalArgumentException("Cannot complete another user's task");
        }

        return task.complete();
    }
}