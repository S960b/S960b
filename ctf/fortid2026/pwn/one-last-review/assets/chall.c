#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define REVIEW_TEXT_SIZE 32

typedef struct review review_t;

struct review {
    char text[REVIEW_TEXT_SIZE];
    void (*display)(review_t *review);
};

typedef struct {
    char contents[sizeof(review_t)];
} draft_t;

static review_t *review;
static review_t *favourite;
static draft_t *draft;

static void show_review(review_t *item)
{
    printf("Your favourite review: %s\n", item->text);
}

__attribute__((used, noinline))
void win(review_t *unused)
{
    char flag[256];
    FILE *file;

    (void)unused;

    file = fopen("flag.txt", "r");
    if (file == NULL) {
        puts("The prize envelope is missing. Please contact the organizers.");
        exit(1);
    }

    if (fgets(flag, sizeof(flag), file) == NULL) {
        puts("The prize envelope is empty. Please contact the organizers.");
        fclose(file);
        exit(1);
    }

    fclose(file);
    printf("Five stars! Here is your prize:\n%s", flag);
    exit(0);
}

static long read_number(void)
{
    char input[64];
    char *end;
    long value;

    if (fgets(input, sizeof(input), stdin) == NULL)
        exit(0);

    errno = 0;
    value = strtol(input, &end, 10);
    if (errno != 0 || end == input)
        return -1;

    return value;
}

static void read_text(char *destination, size_t size)
{
    size_t length;

    if (fgets(destination, size, stdin) == NULL)
        exit(0);

    length = strcspn(destination, "\n");
    if (destination[length] == '\n') {
        destination[length] = '\0';
        return;
    }

    while (getchar() != '\n' && !feof(stdin))
        ;
}

static void read_exact(void *destination, size_t size)
{
    size_t offset = 0;

    while (offset < size) {
        size_t received = fread((char *)destination + offset, 1,
                                size - offset, stdin);
        if (received == 0)
            exit(0);
        offset += received;
    }
}

static void submit_review(void)
{
    if (review != NULL) {
        puts("You already submitted a review.");
        return;
    }

    review = malloc(sizeof(*review));
    if (review == NULL)
        exit(1);

    printf("Review: ");
    read_text(review->text, sizeof(review->text));
    review->display = show_review;
    puts("Review submitted.");
}

static void mark_favourite(void)
{
    if (review == NULL) {
        puts("There is no review to mark as favourite.");
        return;
    }

    favourite = review;
    puts("Review marked as favourite.");
}

static void delete_review(void)
{
    if (review == NULL) {
        puts("There is no review to delete.");
        return;
    }

    free(review);
    review = NULL;
    puts("Review deleted.");
}

static void write_draft(void)
{
    long length;

    if (draft != NULL) {
        puts("You already have a draft.");
        return;
    }

    printf("Draft length (1-%zu): ", sizeof(draft->contents));
    length = read_number();
    if (length < 1 || (size_t)length > sizeof(draft->contents)) {
        puts("Invalid draft length.");
        return;
    }

    draft = malloc(sizeof(*draft));
    if (draft == NULL)
        exit(1);

    memset(draft->contents, 0, sizeof(draft->contents));
    printf("Send exactly %ld draft bytes: ", length);
    read_exact(draft->contents, (size_t)length);
    puts("\nDraft saved.");
}

static void preview_favourite(void)
{
    if (favourite == NULL) {
        puts("You do not have a favourite review.");
        return;
    }

    favourite->display(favourite);
}

static void print_menu(void)
{
    puts("\n1. Submit a review");
    puts("2. Mark review as favourite");
    puts("3. Delete review");
    puts("4. Write a draft");
    puts("5. Preview favourite review");
    puts("6. Leave");
    printf("> ");
}

int main(void)
{
    long choice;

    setvbuf(stdin, NULL, _IONBF, 0);
    setvbuf(stdout, NULL, _IONBF, 0);

    puts("Welcome to One Last Review!");
    puts("We value every review, even after it is gone.");

    while (1) {
        print_menu();
        choice = read_number();

        switch (choice) {
        case 1:
            submit_review();
            break;
        case 2:
            mark_favourite();
            break;
        case 3:
            delete_review();
            break;
        case 4:
            write_draft();
            break;
        case 5:
            preview_favourite();
            break;
        case 6:
            puts("Thanks for your feedback!");
            return 0;
        default:
            puts("Invalid choice.");
        }
    }
}
